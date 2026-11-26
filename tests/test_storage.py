import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from aec_cloud_gateway.discovery import Inverter
from aec_cloud_gateway.gateway import Config, MQTTOutput, emit_sample, encode_state
from aec_cloud_gateway.storage import DatabaseConfig, PostgresStorage


def config_env():
    return {
        "AEC_POLL_ENABLED": "true", "AEC_EMAIL": "test@example.invalid",
        "AEC_PASSWORD": "test", "MQTT_ENABLED": "false", "DB_ENABLED": "true",
        "DB_HOST": "localhost", "DB_NAME": "aec_test", "DB_USER": "aec_test",
        "DB_PASSWORD": "test-only-password",
    }


def test_database_only():
    cfg = Config.from_env(config_env())
    assert not cfg.mqtt_enabled
    assert cfg.database.name == "aec_test"
    assert "test-only-password" not in repr(cfg.database)


def test_no_output():
    with pytest.raises(ValueError, match="at least one output"):
        Config.from_env(config_env() | {"DB_ENABLED": "false"})


@pytest.mark.parametrize("key,value", [
    ("DB_PASSWORD", ""), ("DB_PORT", "0"), ("DB_RETENTION_DAYS", "-1"),
    ("DB_SSLMODE", "invalid"),
])
def test_invalid_db_config(key, value):
    with pytest.raises(ValueError):
        Config.from_env(config_env() | {key: value})


def test_mqtt_offline_does_not_prevent_storage():
    broker = Mock()
    broker.connected.is_set.return_value = False
    storage = Mock()
    device = Inverter("plant", "inverter", None)
    ts = datetime.now(timezone.utc)
    emit_sample([MQTTOutput(broker), storage], device, "summary", {"online": True}, ts)
    storage.attempt.assert_called_once_with("emit", device, "summary", {"online": True}, ts)
    broker.publish.assert_not_called()


def test_database_error_does_not_prevent_mqtt(monkeypatch):
    storage = PostgresStorage(DatabaseConfig.from_env(config_env()))
    def fail():
        raise storage.driver.OperationalError("private error")
    monkeypatch.setattr(storage, "connect", fail)
    mqtt_output = Mock()
    device = Inverter("plant", "inverter", None)
    ts = datetime.now(timezone.utc)
    emit_sample([storage, mqtt_output], device, "summary", {}, ts)
    assert storage.retry_at > 0
    mqtt_output.attempt.assert_called_once_with("emit", device, "summary", {}, ts)


def test_shared_timestamp():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert ts.isoformat() in encode_state({"pv_power_w": 0}, ts)


@pytest.mark.skipif(os.environ.get("AEC_TEST_POSTGRES") != "true",
                    reason="requires disposable PostgreSQL integration database")
def test_postgresql_migration_insert_dedup_retention():
    cfg = DatabaseConfig.from_env(config_env() | {"DB_RETENTION_DAYS": "1"})
    storage = PostgresStorage(cfg)
    device = Inverter("synthetic", "inverter", None)
    now = datetime.now(timezone.utc)
    try:
        storage.connect()
        storage.conn.execute("DELETE FROM aec_snapshots")
        storage.emit(device, "summary", {"pv_power_w": 0, "missing": None}, now)
        storage.emit(device, "summary", {"pv_power_w": 999}, now)
        storage.emit(device, "detail", {"model": "test"}, now)
        storage.cleanup_at = 0
        storage.emit(device, "summary", {}, now - timedelta(days=2))
        rows = storage.conn.execute(
            "SELECT source, payload FROM aec_snapshots ORDER BY source"
        ).fetchall()
        assert rows == [("detail", {"model": "test"}), ("summary", {"pv_power_w": 0, "missing": None})]
        storage.close()
        storage.connect()
        assert storage.conn.execute("SELECT count(*) FROM aec_schema_migrations").fetchone() == (1,)
    finally:
        storage.close()
