"""Read-only, one-inverter MQTT gateway. No subscriptions or control operations."""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import paho.mqtt.client as mqtt

from .client import AECClient
from .discovery import discover, select_inverter
from .errors import AECError, AuthenticationError, RateLimitError
from .telemetry import normalize_summary, normalize_details
from .storage import DatabaseConfig, PostgresStorage
from .health import Health, safe_error

LOG = logging.getLogger("aec-gateway")


def flag(env, key, default="false"):
    value = env.get(key, default).strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"{key} must be true or false")
    return value == "true"


def integer(env, key, default, minimum, maximum):
    try:
        value = int(env.get(key, default))
    except ValueError:
        raise ValueError(f"Invalid {key}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} out of range")
    return value


@dataclass(frozen=True)
class Config:
    email: str = field(repr=False)
    password: str = field(repr=False)
    host: str
    port: int
    username: str = field(repr=False)
    mqtt_password: str = field(repr=False)
    prefix: str
    client_id: str
    tls: bool
    summary_interval: int
    detail_interval: int
    tz: ZoneInfo
    plant_id: str | None = field(repr=False)
    inverter_sn: str | None = field(repr=False)
    mqtt_enabled: bool = True
    database: DatabaseConfig | None = field(default=None, repr=False)
    diagnostics_interval: int = 60
    diagnostics_log_interval: int = 300

    @classmethod
    def from_env(cls, env):
        if not flag(env, "AEC_POLL_ENABLED"):
            raise ValueError("Polling disabled; review provider terms and set AEC_POLL_ENABLED=true")
        mqtt_enabled = flag(env, "MQTT_ENABLED", "true")
        database = DatabaseConfig.from_env(env) if flag(env, "DB_ENABLED") else None
        if not mqtt_enabled and database is None:
            raise ValueError("Enable at least one output")
        if not all(env.get(k) for k in ("AEC_EMAIL", "AEC_PASSWORD")):
            raise ValueError("AEC_EMAIL and AEC_PASSWORD are required")
        if mqtt_enabled and not env.get("MQTT_HOST"):
            raise ValueError("MQTT_HOST is required")
        if mqtt_enabled and bool(env.get("MQTT_USERNAME")) != bool(env.get("MQTT_PASSWORD")):
            raise ValueError("Configure both MQTT_USERNAME and MQTT_PASSWORD, or neither")
        prefix = env.get("MQTT_TOPIC_PREFIX", "aec/inverter")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix):
            raise ValueError("Invalid MQTT_TOPIC_PREFIX")
        cid = env.get("MQTT_CLIENT_ID", "aec-gateway")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
            raise ValueError("Invalid MQTT_CLIENT_ID")
        tls = flag(env, "MQTT_TLS")
        return cls(env["AEC_EMAIL"], env["AEC_PASSWORD"], env.get("MQTT_HOST", ""),
                   integer(env, "MQTT_PORT", 8883 if tls else 1883, 1, 65535),
                   env.get("MQTT_USERNAME", ""), env.get("MQTT_PASSWORD", ""),
                   prefix, cid, tls,
                   integer(env, "AEC_SUMMARY_INTERVAL", 60, 60, 86400),
                   integer(env, "AEC_DETAIL_INTERVAL", 300, 300, 86400),
                   ZoneInfo(env.get("AEC_TIMEZONE", "UTC")),
                   env.get("AEC_PLANT_ID") or None, env.get("AEC_INVERTER_SN") or None,
                   mqtt_enabled, database,
                   integer(env, "AEC_DIAGNOSTICS_INTERVAL", 60, 0, 86400),
                   integer(env, "AEC_DIAGNOSTICS_LOG_INTERVAL",
                           300 if integer(env, "AEC_DIAGNOSTICS_INTERVAL", 60, 0, 86400) else 0,
                           0, 86400))


class BrokerError(Exception):
    pass


class Broker:
    def __init__(self, config):
        self.prefix = config.prefix
        self.connected = threading.Event()
        self.generation = 0
        self.connection_errors = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id,
                                  protocol=mqtt.MQTTv311, clean_session=True)
        if config.username:
            self.client.username_pw_set(config.username, config.mqtt_password)
        if config.tls:
            self.client.tls_set()
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.max_queued_messages_set(20)
        self.client.will_set(self.topic("availability"), "offline", qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_connect_fail = self.on_connect_fail
        self.host, self.port = config.host, config.port

    def topic(self, suffix):
        return f"{self.prefix}/{suffix}"

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.connected.clear()
            self.connection_errors += 1
            LOG.warning("MQTT connection rejected: %s", reason_code)
            return
        self.generation += 1
        self.connected.set()

    def on_connect_fail(self, client, userdata):
        self.connection_errors += 1
        LOG.warning("MQTT transport connection failed")

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def publish(self, suffix, payload, generation):
        if not self.connected.is_set() or self.generation != generation:
            raise BrokerError("MQTT disconnected")
        info = self.client.publish(self.topic(suffix), payload, qos=1, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise BrokerError("MQTT publish rejected")
        try:
            info.wait_for_publish(timeout=5)
            if not info.is_published():
                raise BrokerError("MQTT publish not acknowledged")
        except (RuntimeError, ValueError):
            raise BrokerError("MQTT publish failed") from None
        if not self.connected.is_set() or self.generation != generation:
            raise BrokerError("MQTT connection changed")

    def stop(self):
        try:
            self.publish("availability", "offline", self.generation)
        except BrokerError:
            # Avoid a clean MQTT DISCONNECT if the offline message was not acknowledged.
            # Process exit closes the socket, triggering the broker's Last Will.
            pass
        else:
            self.client.disconnect()
        finally:
            self.client.loop_stop()


@dataclass
class Schedule:
    interval: int
    due: float = 0.0
    failures: int = 0

    def complete(self, now, success):
        if success:
            self.failures = 0
            self.due = now + self.interval
        else:
            self.failures += 1
            delay = max(self.interval, min(3600, 300 * 2 ** min(self.failures - 1, 4)))
            self.due = now + delay


def encode_state(values, fetched_at=None):
    return json.dumps({"schema": 1, "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
                       **values}, allow_nan=False, separators=(",", ":"))


def publish_source(broker, source, values, generation, fetched_at=None):
    broker.publish(source + "/state", encode_state(values, fetched_at), generation)
    broker.publish(source + "/availability", "online", generation)


def poll_source(client, device, source, tz):
    if source == "summary":
        return normalize_summary(client.get_home_count_data(device.plant_id, device.serial))
    return normalize_details(client.get_device_by_sn(device.serial, datetime.now(tz).date()))


class MQTTOutput:
    def __init__(self, broker):
        self.broker = broker
        self.generation = 0
        self.published = 0
        self.skipped = 0
        self.publish_errors = 0
        self.diagnostic_errors = 0
        self.last_success_at = None

    def sync(self):
        if not self.broker.connected.is_set():
            self.generation = 0
            return False
        current = self.broker.generation
        if current != self.generation:
            self.broker.publish("availability", "offline", current)
            for source in ("summary", "detail"):
                self.broker.publish(source + "/availability", "offline", current)
            self.broker.publish("availability", "online", current)
            self.generation = current
        return True

    def emit(self, device, source, values, fetched_at):
        if self.sync():
            publish_source(self.broker, source, values, self.generation, fetched_at)
            self.published += 1
            self.last_success_at = fetched_at.isoformat()
            LOG.info("%s published", source)
        else:
            self.skipped += 1

    def health(self):
        return {"enabled": True, "connected": self.broker.connected.is_set(),
                "connections": self.broker.generation,
                "connection_errors": self.broker.connection_errors,
                "published": self.published, "skipped": self.skipped,
                "publish_errors": self.publish_errors,
                "diagnostic_errors": self.diagnostic_errors,
                "last_success_at": self.last_success_at}

    def report(self, payload):
        if not self.broker.connected.is_set() or self.generation != self.broker.generation:
            return False
        try:
            self.broker.publish("diagnostics", json.dumps(payload, allow_nan=False, separators=(",", ":")), self.generation)
        except BrokerError:
            self.diagnostic_errors += 1
            LOG.warning("MQTT diagnostics publish failed")
            return False
        return True

    def offline(self, sources):
        if self.sync():
            for source in sources:
                self.broker.publish(source + "/availability", "offline", self.generation)

    def attempt(self, action, *args):
        try:
            getattr(self, action)(*args)
        except BrokerError as exc:
            self.generation = 0
            self.publish_errors += 1
            LOG.warning("MQTT publish interrupted (%s): %s", action, exc)


def emit_sample(outputs, device, source, values, fetched_at):
    for output in outputs:
        output.attempt("emit", device, source, values, fetched_at)


def run(config, stop):
    broker = Broker(config) if config.mqtt_enabled else None
    mqtt_output = MQTTOutput(broker) if broker else None
    database = PostgresStorage(config.database) if config.database else None
    outputs = [output for output in (database, mqtt_output) if output is not None]
    schedules = {"summary": Schedule(config.summary_interval),
                 "detail": Schedule(config.detail_interval)}
    discovery = Schedule(300)
    device = None
    summary_ok = False
    halted = False
    health = Health()
    next_report = 0.0
    next_log = 0.0
    if broker:
        broker.start()
    try:
        with AECClient(config.email, config.password, timeout=10, max_retries=1) as client:
            while not stop.is_set():
                if mqtt_output:
                    mqtt_output.attempt("sync")
                now = time.monotonic()
                mqtt_due = bool(mqtt_output and config.diagnostics_interval and now >= next_report)
                log_due = bool(config.diagnostics_log_interval and now >= next_log)
                if mqtt_due or log_due:
                    payload = health.snapshot(client, schedules, discovery, device is not None,
                                              summary_ok, halted, mqtt_output, database, now)
                    if mqtt_due:
                        mqtt_output.report(payload)
                        next_report = time.monotonic() + config.diagnostics_interval
                    if log_due:
                        LOG.info("Diagnostics: %s", json.dumps(payload, separators=(",", ":")))
                        next_log = time.monotonic() + config.diagnostics_log_interval
                if halted:
                    stop.wait(1)
                    continue
                if device is None and time.monotonic() >= discovery.due:
                    started = time.monotonic()
                    try:
                        device = select_inverter(discover(client, config.plant_id), config.inverter_sn)
                        discovery.complete(time.monotonic(), True)
                        health.record("discovery", started)
                        LOG.info("AEC device selected")
                    except (AuthenticationError, RateLimitError) as exc:
                        health.record("discovery", started, exc)
                        halted = True
                        LOG.error("AEC polling suspended until restart: %s", safe_error(exc))
                    except AECError as exc:
                        health.record("discovery", started, exc)
                        discovery.complete(time.monotonic(), False)
                        LOG.warning("Discovery failed: %s", safe_error(exc))
                if device is None:
                    stop.wait(1)
                    continue
                for source, schedule in schedules.items():
                    if stop.is_set() or halted:
                        break
                    if time.monotonic() < schedule.due or (source == "detail" and not summary_ok):
                        continue
                    started = time.monotonic()
                    try:
                        values = poll_source(client, device, source, config.tz)
                        fetched_at = datetime.now(timezone.utc)
                        schedule.complete(time.monotonic(), True)
                        if source == "summary":
                            summary_ok = values.get("online") is True
                            if not summary_ok:
                                health.record(source, started, offline=True)
                                if mqtt_output:
                                    mqtt_output.attempt("offline", ("summary", "detail"))
                                schedule.complete(time.monotonic(), False)
                                continue
                        health.record(source, started)
                        emit_sample(outputs, device, source, values, fetched_at)
                    except (AuthenticationError, RateLimitError) as exc:
                        health.record(source, started, exc)
                        halted = True
                        summary_ok = False
                        if mqtt_output:
                            mqtt_output.attempt("offline", tuple(schedules))
                        LOG.error("AEC polling suspended until restart: %s", safe_error(exc))
                    except AECError as exc:
                        health.record(source, started, exc)
                        schedule.complete(time.monotonic(), False)
                        if source == "summary":
                            summary_ok = False
                        if mqtt_output:
                            mqtt_output.attempt("offline", ("summary", "detail") if source == "summary" else (source,))
                        LOG.warning("%s failed: %s; retry in %.0fs", source, safe_error(exc), max(0, schedule.due - time.monotonic()))
                stop.wait(1)
    finally:
        try:
            if database:
                database.close()
        finally:
            if broker:
                broker.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = Config.from_env(os.environ)
    except (ValueError, KeyError, ZoneInfoNotFoundError) as exc:
        LOG.error("Configuration error: %s", str(exc))
        return 2
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(config, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
