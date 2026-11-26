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
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

from .client import AECClient
from .discovery import discover, select_inverter
from .errors import AECError, AuthenticationError, RateLimitError
from .telemetry import normalize_summary, normalize_details

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

    @classmethod
    def from_env(cls, env):
        if not flag(env, "AEC_POLL_ENABLED"):
            raise ValueError("Polling disabled; review provider terms and set AEC_POLL_ENABLED=true")
        if not all(env.get(k) for k in ("AEC_EMAIL", "AEC_PASSWORD", "MQTT_HOST")):
            raise ValueError("AEC_EMAIL, AEC_PASSWORD and MQTT_HOST are required")
        if bool(env.get("MQTT_USERNAME")) != bool(env.get("MQTT_PASSWORD")):
            raise ValueError("Configure both MQTT_USERNAME and MQTT_PASSWORD, or neither")
        prefix = env.get("MQTT_TOPIC_PREFIX", "aec/inverter")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix):
            raise ValueError("Invalid MQTT_TOPIC_PREFIX")
        cid = env.get("MQTT_CLIENT_ID", "aec-gateway")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
            raise ValueError("Invalid MQTT_CLIENT_ID")
        tls = flag(env, "MQTT_TLS")
        return cls(env["AEC_EMAIL"], env["AEC_PASSWORD"], env["MQTT_HOST"],
                   integer(env, "MQTT_PORT", 8883 if tls else 1883, 1, 65535),
                   env.get("MQTT_USERNAME", ""), env.get("MQTT_PASSWORD", ""),
                   prefix, cid, tls,
                   integer(env, "AEC_SUMMARY_INTERVAL", 60, 60, 86400),
                   integer(env, "AEC_DETAIL_INTERVAL", 300, 300, 86400),
                   ZoneInfo(env.get("AEC_TIMEZONE", "UTC")),
                   env.get("AEC_PLANT_ID") or None, env.get("AEC_INVERTER_SN") or None)


class BrokerError(Exception):
    pass


class Broker:
    def __init__(self, config):
        self.prefix = config.prefix
        self.connected = threading.Event()
        self.generation = 0
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
        self.host, self.port = config.host, config.port

    def topic(self, suffix):
        return f"{self.prefix}/{suffix}"

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.connected.clear()
            LOG.warning("MQTT connection rejected")
            return
        self.generation += 1
        self.connected.set()

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


def encode_state(values):
    return json.dumps({"schema": 1, "fetched_at": datetime.now(timezone.utc).isoformat(),
                       **values}, allow_nan=False, separators=(",", ":"))


def publish_source(broker, source, values, generation):
    broker.publish(source + "/state", encode_state(values), generation)
    broker.publish(source + "/availability", "online", generation)


def poll_source(client, device, source, tz):
    if source == "summary":
        return normalize_summary(client.get_home_count_data(device.plant_id, device.serial))
    return normalize_details(client.get_device_by_sn(device.serial, datetime.now(tz).date()))


def run(config, stop):
    broker = Broker(config)
    broker.start()
    generation = 0
    schedules = {"summary": Schedule(config.summary_interval),
                 "detail": Schedule(config.detail_interval)}
    discovery = Schedule(300)
    device = None
    summary_ok = False
    halted = False
    try:
        with AECClient(config.email, config.password, timeout=10, max_retries=1) as client:
            while not stop.is_set():
                if not broker.connected.is_set():
                    stop.wait(1)
                    continue
                try:
                    if generation != broker.generation:
                        current = broker.generation
                        broker.publish("availability", "offline", current)
                        for source in schedules:
                            broker.publish(source + "/availability", "offline", current)
                        broker.publish("availability", "online", current)
                        generation = current
                        summary_ok = False
                    if halted:
                        stop.wait(1)
                        continue
                    now = time.monotonic()
                    if device is None and now >= discovery.due:
                        try:
                            device = select_inverter(discover(client, config.plant_id), config.inverter_sn)
                            discovery.complete(time.monotonic(), True)
                            LOG.info("AEC device selected")
                        except (AuthenticationError, RateLimitError):
                            halted = True
                            LOG.error("AEC authentication or rate limit; polling suspended until restart")
                        except AECError as exc:
                            discovery.complete(time.monotonic(), False)
                            LOG.warning("Discovery failed: %s", type(exc).__name__)
                    if device is None:
                        stop.wait(1)
                        continue
                    for source, schedule in schedules.items():
                        if stop.is_set() or halted or not broker.connected.is_set():
                            break
                        if time.monotonic() < schedule.due or (source == "detail" and not summary_ok):
                            continue
                        try:
                            values = poll_source(client, device, source, config.tz)
                            schedule.complete(time.monotonic(), True)
                            if source == "summary":
                                summary_ok = values.get("online") is True
                                if not summary_ok:
                                    broker.publish("summary/availability", "offline", generation)
                                    broker.publish("detail/availability", "offline", generation)
                                    schedule.complete(time.monotonic(), False)
                                    continue
                            publish_source(broker, source, values, generation)
                            LOG.info("%s published", source)
                        except (AuthenticationError, RateLimitError):
                            halted = True
                            summary_ok = False
                            for key in schedules:
                                broker.publish(key + "/availability", "offline", generation)
                            LOG.error("AEC authentication or rate limit; polling suspended until restart")
                        except AECError as exc:
                            schedule.complete(time.monotonic(), False)
                            if source == "summary":
                                summary_ok = False
                            broker.publish(source + "/availability", "offline", generation)
                            if source == "summary":
                                broker.publish("detail/availability", "offline", generation)
                            LOG.warning("%s failed: %s", source, type(exc).__name__)
                except BrokerError:
                    # Reset retained source availability before the next successful publish.
                    generation = 0
                    summary_ok = False
                    LOG.warning("MQTT publish interrupted")
                stop.wait(1)
    finally:
        broker.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = Config.from_env(os.environ)
    except (ValueError, KeyError) as exc:
        LOG.error("Configuration error: %s", str(exc))
        return 2
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(config, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
