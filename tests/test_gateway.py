import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from aec_cloud_gateway.gateway import Config, Schedule, encode_state, publish_source


@pytest.fixture
def env():
    return {"AEC_POLL_ENABLED": "true", "AEC_EMAIL": "test@example.invalid",
            "AEC_PASSWORD": "fake", "MQTT_HOST": "broker"}


def test_defaults(env):
    cfg = Config.from_env(env)
    assert (cfg.port, cfg.summary_interval, cfg.detail_interval) == (1883, 60, 300)
    assert cfg.prefix == "aec/inverter"
    assert "test@example.invalid" not in repr(cfg)
    assert "fake" not in repr(cfg)


@pytest.mark.parametrize("key,value", [
    ("AEC_POLL_ENABLED", "false"), ("MQTT_TOPIC_PREFIX", "aec/#"),
    ("AEC_SUMMARY_INTERVAL", "1"), ("AEC_DETAIL_INTERVAL", "0"),
    ("MQTT_PORT", "65536"), ("MQTT_TLS", "yes"),
    ("MQTT_USERNAME", "no-password"),
])
def test_invalid_config(env, key, value):
    env[key] = value
    with pytest.raises(ValueError):
        Config.from_env(env)


def test_tls(env):
    env["MQTT_TLS"] = "true"
    assert Config.from_env(env).port == 8883


def test_schedule():
    summary = Schedule(60)
    detail = Schedule(300)
    summary.complete(100, True)
    detail.complete(100, True)
    assert (summary.due, detail.due) == (160, 400)
    summary.complete(160, False)
    assert summary.due == 460
    summary.complete(460, False)
    assert summary.due == 1060
    summary.complete(1060, True)
    assert summary.due == 1120 and summary.failures == 0


def test_state_preserves_null_and_zero():
    data = json.loads(encode_state({"zero": 0, "missing": None}))
    assert data["zero"] == 0 and data["missing"] is None and data["schema"] == 1
    assert datetime.fromisoformat(data["fetched_at"]).tzinfo == timezone.utc


def test_publish_order():
    broker = Mock()
    publish_source(broker, "summary", {"battery_soc_percent": 24}, 1)
    calls = broker.publish.call_args_list
    assert calls[0].args[0] == "summary/state"
    assert json.loads(calls[0].args[1])["battery_soc_percent"] == 24
    assert calls[1].args == ("summary/availability", "online", 1)


def test_failed_state_does_not_mark_available():
    broker = Mock()
    broker.publish.side_effect = RuntimeError("test")
    with pytest.raises(RuntimeError):
        publish_source(broker, "detail", {}, 1)
    assert broker.publish.call_count == 1
