import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from aec_cloud_gateway.errors import TransportError
from aec_cloud_gateway.gateway import BrokerError, Config, MQTTOutput, Schedule
from aec_cloud_gateway.health import Health, PollStats, safe_error
from aec_cloud_gateway.storage import DatabaseConfig, PostgresStorage


def test_safe_error():
    assert safe_error(TransportError("HTTP 503: retry budget exhausted")) == "TransportError: HTTP 503: retry budget exhausted"
    assert safe_error(TransportError("Timeout: network retry budget exhausted")).startswith("TransportError: Timeout")
    assert safe_error(TransportError("private credentials")) == "TransportError"


def test_poll_recovery():
    stats = PollStats()
    stats.record(0.1)
    stats.record(0.2, TransportError("HTTP 503: retry budget exhausted"))
    stats.record(0.3, offline=True)
    stats.record(0.4)
    result = stats.snapshot(Schedule(60, due=100), 90)
    assert result["successful_polls"] == 2
    assert result["failed_polls"] == 2
    assert result["offline_polls"] == 1
    assert result["consecutive_failures"] == 0
    assert result["last_error"] == "InverterOfflineOrUnknown"
    assert result["avg_duration_ms"] == 250
    assert result["next_poll_in_seconds"] == 10
    assert stats.snapshot(Schedule(60), 90, True)["next_poll_in_seconds"] is None


def test_report_disabled_outputs_and_halted():
    h = Health()
    client = Mock(request_attempts=4, request_retries=1, successful_logins=1, session_renewals=0)
    client.session_health.snapshot.return_value = {}
    data = h.snapshot(client, {"summary": Schedule(60), "detail": Schedule(300)},
                      Schedule(300), True, False, True, None, None, h.started + 20)
    assert data["uptime_seconds"] == 20
    assert data["polling_halted"] is True
    assert data["summary"]["blocked"] is True
    assert data["detail"]["blocked"] is True
    assert data["mqtt"] == data["postgresql"] == {"enabled": False}
    assert data["api"]["request_retries"] == 1
    json.dumps(data, allow_nan=False)


def test_diagnostics_publish_failure_isolated():
    broker = Mock(generation=1)
    broker.connected.is_set.return_value = True
    output = MQTTOutput(broker)
    output.generation = 1
    broker.publish.side_effect = BrokerError("test")
    assert output.report({"schema": 1}) is False
    assert output.diagnostic_errors == 1
    assert output.publish_errors == 0
    assert output.generation == 1
    broker.publish.side_effect = None
    assert output.report({"schema": 1}) is True
    assert broker.publish.call_args.args == ("diagnostics", '{"schema":1}', 1)


def test_mqtt_skipped_sample():
    broker = Mock()
    broker.connected.is_set.return_value = False
    output = MQTTOutput(broker)
    output.attempt("emit", None, "summary", {}, datetime.now(timezone.utc))
    assert output.skipped == 1
    assert output.published == 0


def test_database_backoff_counters(monkeypatch):
    storage = PostgresStorage(DatabaseConfig("db", 5432, "test", "test", "private"))
    def fail():
        raise storage.driver.OperationalError("private")
    monkeypatch.setattr(storage, "connect", fail)
    now = datetime.now(timezone.utc)
    storage.attempt("emit", None, "summary", {}, now)
    storage.attempt("emit", None, "summary", {}, now)
    assert storage.write_errors == 1
    assert storage.skipped == 1
    assert storage.stored == 0


def test_diagnostics_interval():
    env = {"AEC_POLL_ENABLED": "true", "AEC_EMAIL": "fake", "AEC_PASSWORD": "fake",
           "MQTT_HOST": "broker"}
    assert Config.from_env(env).diagnostics_interval == 60
    assert Config.from_env(env | {"AEC_DIAGNOSTICS_INTERVAL": "0"}).diagnostics_interval == 0
    with pytest.raises(ValueError):
        Config.from_env(env | {"AEC_DIAGNOSTICS_INTERVAL": "-1"})

def test_independent_log_interval():
    env = {"AEC_POLL_ENABLED": "true", "AEC_EMAIL": "fake", "AEC_PASSWORD": "fake",
           "MQTT_HOST": "broker"}
    assert Config.from_env(env).diagnostics_log_interval == 300
    assert Config.from_env(env | {"AEC_DIAGNOSTICS_INTERVAL": "0"}).diagnostics_log_interval == 0
    cfg = Config.from_env(env | {"AEC_DIAGNOSTICS_INTERVAL": "0",
                                 "AEC_DIAGNOSTICS_LOG_INTERVAL": "60"})
    assert cfg.diagnostics_interval == 0 and cfg.diagnostics_log_interval == 60
    with pytest.raises(ValueError):
        Config.from_env(env | {"AEC_DIAGNOSTICS_LOG_INTERVAL": "-1"})
