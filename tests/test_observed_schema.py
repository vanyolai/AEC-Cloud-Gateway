import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from aec_cloud_gateway.discovery import discover, select_inverter
from aec_cloud_gateway.errors import ProtocolError
from aec_cloud_gateway.telemetry import normalize, normalize_summary, quantity
from aec_cloud_gateway.snapshot import main

FIXTURE = Path(__file__).parent / "fixtures" / "observed_schema.json"


@pytest.fixture
def capture():
    return json.loads(FIXTURE.read_text())


def test_discover(capture):
    c = Mock()
    c.get_plants.return_value = capture["get_plants"]
    c.get_home_control_sn.return_value = capture["get_home_control_sn"]
    device = select_inverter(discover(c))
    assert device.plant_id == "101"
    assert device.serial == "EXAMPLE-INVERTER"
    assert device.logger_serial == "EXAMPLE-LOGGER"
    c.get_home_control_sn.assert_called_once_with("101")


def test_ambiguous_discovery(capture):
    c = Mock()
    c.get_plants.return_value = capture["get_plants"]
    response = capture["get_home_control_sn"]
    response["obj"].append({"plantId": 101, "deviceSn": "OTHER"})
    c.get_home_control_sn.return_value = response
    devices = discover(c)
    with pytest.raises(ProtocolError):
        select_inverter(devices)
    assert select_inverter(devices, "OTHER").serial == "OTHER"


def test_invalid_envelope():
    c = Mock()
    c.get_plants.return_value = {"result": 0, "data": []}
    with pytest.raises(ProtocolError):
        discover(c)


def test_normalize(capture):
    values = normalize(capture["get_home_count_data"], capture["get_device_by_sn"])
    assert values["battery_soc_percent"] == 42
    assert values["detail_battery_soc_percent"] == 42
    assert values["battery_power_w"] == 20
    assert values["battery_direction"] == "discharging"
    assert values["battery_capacity_kwh"] is None
    assert values["grid_power_reported_w"] == 0
    assert values["inverter_temperature_c"] == 21
    assert values["load_energy_total_kwh"] == 12.5
    assert values["output_frequency_hz"] == 50
    assert values["online"] is True


def test_unknown_direction(capture):
    summary = capture["get_home_count_data"]
    summary["obj"]["batWorkMode"] = 1
    result = normalize_summary(summary)
    assert result["battery_power_reported_w"] == 20
    assert result["battery_power_w"] is None
    assert result["battery_direction"] is None


def test_missing_and_zero():
    result = normalize_summary({"result": 0, "obj": {"solarPower": "0W", "batSoc": "0%"}})
    assert result["pv_power_w"] == 0
    assert result["battery_soc_percent"] == 0
    assert result["battery_voltage_v"] is None


@pytest.mark.parametrize(("raw", "unit", "expected"), [
    ("1.2kW", "W", 1200), ("250Wh", "kWh", .25), ("0W", "W", 0),
    ("--", "W", None), (None, "W", None), ("10A", "W", None),
    ("19.0℃", "°C", 19), ("NaNW", "W", None),
])
def test_quantity(raw, unit, expected):
    assert quantity(raw, unit) == expected


def test_snapshot_capture(capsys):
    assert main(["--capture", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["battery_soc_percent"] == 42
    assert "EXAMPLE-INVERTER" not in out
