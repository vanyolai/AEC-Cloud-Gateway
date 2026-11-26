import json
from unittest.mock import MagicMock

import pytest

from aec_cloud_gateway import AuthenticationError
from aec_cloud_gateway import diagnostics


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("AEC_EMAIL", "private@example.invalid")
    monkeypatch.setenv("AEC_PASSWORD", "private-password")
    answers = iter(["12345", "PRIVATE-SUMMARY", "PRIVATE-INVERTER"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cls = MagicMock()
    c = cls.return_value.__enter__.return_value
    c.get_plants.return_value = {"data": [{"plantId": "12345", "name": "PRIVATE-NAME"}]}
    c.get_home_control_sn.return_value = {"data": "PRIVATE-SUMMARY"}
    c.get_home_count_data.return_value = {"data": {"popupData": {"secret-key": "secret-value"}}}
    c.get_device_by_sn.return_value = {"data": {"deviceDetail": None, "deviceInfoMap": {}}}
    monkeypatch.setattr(diagnostics, "AECClient", cls)
    return c


def test_describe_does_not_expose_values_or_unknown_keys():
    assert diagnostics.describe({"secret-key": {"data": [1, {"unit": "secret"}]}}) == {
        "root": "dict", "fields": {"data": ["list"], "unit": ["str"]}}


def test_live_flow_redacted(fake, capsys):
    assert diagnostics.main(["--day", "2026-11-26"]) == 0
    fake.login.assert_called_once()
    fake.get_home_count_data.assert_called_once_with("12345", "PRIVATE-SUMMARY")
    assert fake.get_device_by_sn.call_args.args[0] == "PRIVATE-INVERTER"
    assert fake.get_device_by_sn.call_args.args[1].isoformat() == "2026-11-26"
    out = capsys.readouterr().out
    assert "Smoke test: OK" in out and "deviceDetail" in out
    assert all(s not in out for s in ["PRIVATE", "12345", "secret-key", "secret-value", "private-password"])


def test_capture_is_private_and_not_overwritten(fake, tmp_path, capsys):
    path = tmp_path / "capture.json"
    assert diagnostics.main(["--capture", str(path)]) == 0
    content = path.read_bytes()
    assert path.stat().st_mode & 0o077 == 0
    assert json.loads(content)["get_plants"]["data"][0]["plantId"] == "12345"
    assert b"private-password" not in content
    assert diagnostics.main(["--capture", str(path)]) == 2
    assert path.read_bytes() == content
    fake.login.assert_called_once()


def test_failure_saves_partial_capture(fake, tmp_path, capsys):
    fake.get_home_control_sn.side_effect = AuthenticationError("PRIVATE-SECRET")
    path = tmp_path / "partial.json"
    assert diagnostics.main(["--capture", str(path)]) == 1
    assert list(json.loads(path.read_text())) == ["get_plants"]
    assert "PRIVATE-SECRET" not in capsys.readouterr().err
    fake.get_home_count_data.assert_not_called()


def test_private_discovery_requires_opt_in(fake, capsys):
    assert diagnostics.main(["--show-private-discovery"]) == 0
    out = capsys.readouterr().out
    assert "PRIVATE-NAME" in out and "PRIVATE-SUMMARY" in out
    assert "secret-value" not in out
