import json
from unittest.mock import Mock

import pytest

from aec_cloud_gateway.gateway import BrokerError, Config
from aec_cloud_gateway.ha_discovery import DiscoveryConfig, DiscoveryPublisher, discovery_messages
from aec_cloud_gateway.telemetry import normalize_summary, normalize_details


def env():
    return {"AEC_POLL_ENABLED": "true", "AEC_EMAIL": "fake", "AEC_PASSWORD": "fake",
            "MQTT_HOST": "broker"}


def test_opt_in_and_validation():
    assert Config.from_env(env()).ha_discovery is None
    cfg = Config.from_env(env() | {"HA_DISCOVERY_ENABLED": "true"})
    assert cfg.ha_discovery.device_id.startswith("aec_")
    assert cfg.ha_discovery == Config.from_env(env() | {"HA_DISCOVERY_ENABLED": "true"}).ha_discovery
    for extra in ({"HA_DISCOVERY_PREFIX": "ha/#"}, {"HA_DEVICE_ID": "x/y"},
                  {"HA_DISCOVERY_REMOVE": "true"}, {"HA_DEVICE_NAME": ""}):
        with pytest.raises(ValueError):
            Config.from_env(env() | {"HA_DISCOVERY_ENABLED": "true"} | extra)


def test_configs_cover_normalized_fields():
    cfg = DiscoveryConfig("homeassistant", "aec_test", "Inverter")
    configs = {topic.split("/")[-2]: json.loads(payload)
               for topic, payload in discovery_messages(cfg, "aec/inverter", 60, 300)}
    summary = normalize_summary({"result": 0, "obj": {}})
    detail = normalize_details({"result": 0, "obj": {}})
    assert set(configs) == (set(summary) | set(detail)) - {"online"}
    assert len(configs) == 50
    assert len({c["unique_id"] for c in configs.values()}) == 50
    for key, c in configs.items():
        source = "summary" if key in summary else "detail"
        assert c["state_topic"] == f"aec/inverter/{source}/state"
        assert c["availability_mode"] == "all"
        assert c["availability"] == [{"topic": "aec/inverter/availability"},
                                     {"topic": f"aec/inverter/{source}/availability"}]
        assert c["expire_after"] == (180 if source == "summary" else 900)
        assert c["value_template"] == "{{ value_json.get('" + key + "') }}"
        assert c["device"]["identifiers"] == ["aec_test"]
        assert "command_topic" not in c
    assert configs["battery_soc_percent"]["device_class"] == "battery"
    assert configs["pv_energy_total_kwh"]["state_class"] == "total_increasing"
    assert "state_class" not in configs["pv_energy_today_kwh"]
    assert configs["grid_power_reported_w"]["device_class"] == "power"


def test_removal_matches_config_topics():
    normal = list(discovery_messages(DiscoveryConfig("ha", "x", "X"), "aec/test", 60, 300))
    removal = list(discovery_messages(DiscoveryConfig("ha", "x", "X", True), "aec/test", 60, 300))
    assert removal == [(topic, "") for topic, _ in normal]


def test_reconnect_and_independent_retry():
    p = DiscoveryPublisher(DiscoveryConfig("ha", "x", "X"), "aec/test", 60, 300)
    broker = Mock()
    broker.publish_topic.side_effect = BrokerError("test")
    p.step(broker, 1, 0, BrokerError)
    assert p.health()["publish_errors"] == 1 and p.index == 0
    p.step(broker, 1, 59, BrokerError)
    assert broker.publish_topic.call_count == 1
    broker.publish_topic.side_effect = None
    p.step(broker, 1, 60, BrokerError)
    assert p.index == 1
    for n in range(49):
        p.step(broker, 1, 61 + n, BrokerError)
    assert p.health()["complete"]
    p.step(broker, 2, 120, BrokerError)
    assert p.index == 1
