"""Optional retained Home Assistant discovery; no cloud requests or commands."""
from dataclasses import dataclass
import hashlib
import json
import logging
import re

from .telemetry import SUMMARY, POPUP, DETAIL

LOG = logging.getLogger("aec-gateway")
_TOPIC = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class DiscoveryConfig:
    prefix: str
    device_id: str
    name: str
    remove: bool = False

    @classmethod
    def from_env(cls, env, mqtt_enabled, telemetry_prefix):
        def flag(key):
            value = env.get(key, "false").strip().lower()
            if value not in ("true", "false"):
                raise ValueError(f"{key} must be true or false")
            return value == "true"
        enabled, remove = flag("HA_DISCOVERY_ENABLED"), flag("HA_DISCOVERY_REMOVE")
        if enabled and remove:
            raise ValueError("HA discovery enable and removal are mutually exclusive")
        if not enabled and not remove:
            return None
        if not mqtt_enabled:
            raise ValueError("HA discovery requires MQTT_ENABLED=true")
        prefix = env.get("HA_DISCOVERY_PREFIX", "homeassistant")
        if not _TOPIC.fullmatch(prefix):
            raise ValueError("Invalid HA_DISCOVERY_PREFIX")
        default_id = "aec_" + hashlib.sha256(telemetry_prefix.encode()).hexdigest()[:20]
        device_id = env.get("HA_DEVICE_ID") or default_id
        if not _ID.fullmatch(device_id):
            raise ValueError("Invalid HA_DEVICE_ID")
        name = env.get("HA_DEVICE_NAME", "AEC Inverter").strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError("Invalid HA_DEVICE_NAME")
        return cls(prefix, device_id, name, remove)


# Only normalized telemetry keys; reported and signed values remain distinct.
_CLASSES = {"W": "power", "V": "voltage", "A": "current", "Hz": "frequency",
            "°C": "temperature", "kWh": "energy"}
_TEXT = ("model", "firmware", "operating_mode", "battery_status", "bms_status",
         "grid_status")


def sensor_specs():
    for source, mapping in (("summary", SUMMARY), ("summary", POPUP), ("detail", DETAIL)):
        for key, fields in mapping.items():
            yield source, key, fields[-1]
    for key, unit in (("battery_soc_percent", "%"), ("battery_power_w", "W"),
                      ("battery_work_mode", None), ("battery_direction", None)):
        yield "summary", key, unit
    yield "detail", "detail_battery_soc_percent", "%"
    for key in _TEXT:
        yield "detail", key, None


def discovery_messages(config, telemetry_prefix, summary_interval, detail_interval):
    device = {"identifiers": [config.device_id], "name": config.name,
              "manufacturer": "AEC", "model": "Cloud inverter"}
    for source, key, unit in sensor_specs():
        topic = f"{config.prefix}/sensor/{config.device_id}/{key}/config"
        if config.remove:
            yield topic, ""
            continue
        interval = summary_interval if source == "summary" else detail_interval
        payload = {
            "name": key.removesuffix("_" + {"W":"w", "V":"v", "A":"a", "Hz":"hz",
                    "°C":"c", "kWh":"kwh", "%":"percent"}.get(unit, "")).replace("_", " ").capitalize(),
            "unique_id": f"{config.device_id}_{key}",
            "device": device,
            "state_topic": f"{telemetry_prefix}/{source}/state",
            "value_template": "{{ value_json.get('" + key + "') }}",
            "availability": [{"topic": f"{telemetry_prefix}/availability"},
                             {"topic": f"{telemetry_prefix}/{source}/availability"}],
            "availability_mode": "all",
            "expire_after": 3 * interval,
            "qos": 1,
        }
        if unit:
            payload["unit_of_measurement"] = unit
            if unit in _CLASSES:
                payload["device_class"] = _CLASSES[unit]
            elif key in ("battery_soc_percent", "detail_battery_soc_percent"):
                payload["device_class"] = "battery"
            if key.endswith("_energy_total_kwh"):
                payload["state_class"] = "total_increasing"
            elif unit != "kWh":
                payload["state_class"] = "measurement"
        if key in ("model", "firmware", "battery_work_mode", "detail_battery_soc_percent"):
            payload["entity_category"] = "diagnostic"
            payload["enabled_by_default"] = False
        yield topic, json.dumps(payload, separators=(",", ":"), allow_nan=False)


class DiscoveryPublisher:
    """Publish at most one config per loop, retry separately on failures."""
    def __init__(self, config, telemetry_prefix, summary_interval, detail_interval):
        self.config = config
        self.messages = list(discovery_messages(config, telemetry_prefix, summary_interval, detail_interval))
        self.generation = 0
        self.index = 0
        self.retry_at = 0.0
        self.errors = 0

    def step(self, broker, generation, now, error_type):
        if generation != self.generation:
            self.generation = generation
            self.index = 0
            self.retry_at = 0.0
        if self.index >= len(self.messages) or now < self.retry_at:
            return
        topic, payload = self.messages[self.index]
        try:
            broker.publish_topic(topic, payload, generation)
        except error_type:
            self.errors += 1
            self.retry_at = now + 60
            LOG.warning("HA discovery publish failed; retry deferred")
            return
        self.index += 1
        if self.index == len(self.messages):
            LOG.info("HA discovery %s: %d configs", "removed" if self.config.remove else "published", self.index)

    def health(self):
        return {"enabled": not self.config.remove, "removing": self.config.remove,
                "configs_completed": self.index, "configs_total": len(self.messages),
                "publish_errors": self.errors, "complete": self.index == len(self.messages)}
