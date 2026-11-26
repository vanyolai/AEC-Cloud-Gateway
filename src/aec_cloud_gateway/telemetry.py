"""Conservative telemetry mapping for observed SP100 responses."""
from __future__ import annotations

import math
import re

from .discovery import object_payload

_QUANTITY = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(\S+)")


def quantity(value, unit):
    """Return a finite number in the requested unit, or None; never guess."""
    if not isinstance(value, str):
        return None
    match = _QUANTITY.fullmatch(value.strip())
    if not match:
        return None
    num, actual = match.groups()
    scales = {("kW", "W"): 1000, ("Wh", "kWh"): .001, ("MWh", "kWh"): 1000,
              ("℃", "°C"): 1}
    scale = 1 if actual == unit else scales.get((actual, unit))
    if scale is None:
        return None
    number = float(num) * scale
    return number if math.isfinite(number) else None


def _map(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) and value.strip() not in ("", "--") else None


def _soc(value):
    n = quantity(value, "%")
    return n if n is not None and 0 <= n <= 100 else None


# Stable public keys, units and actual field names; no guessed numeric conversions.
SUMMARY = {
    "pv_power_w": ("solarPower", "W"),
    "grid_power_reported_w": ("gridPower", "W"),
    "load_power_w": ("loadPower", "W"),
    "battery_power_reported_w": ("batPower", "W"),
    "pv_energy_today_kwh": ("todayEnergy", "kWh"),
    "pv_energy_month_kwh": ("monthEnergy", "kWh"),
    "pv_energy_year_kwh": ("yearEnergy", "kWh"),
    "pv_energy_total_kwh": ("totalEnergy", "kWh"),
    "battery_remaining_kwh": ("batRemainingEnergy", "kWh"),
    "battery_capacity_kwh": ("batTotalCapacity", "kWh"),
}
POPUP = {
    "pv1_voltage_v": ("pvInfo", "pv1Voltage", "V"),
    "pv1_current_a": ("pvInfo", "pv1Current", "A"),
    "pv1_power_w": ("pvInfo", "pv1Power", "W"),
    "pv2_voltage_v": ("pvInfo", "pv2Voltage", "V"),
    "pv2_current_a": ("pvInfo", "pv2Current", "A"),
    "pv2_power_w": ("pvInfo", "pv2Power", "W"),
    "grid_voltage_v": ("gridInfo", "gridAVoltage", "V"),
    "grid_current_a": ("gridInfo", "gridACurrent", "A"),
    "grid_frequency_hz": ("gridInfo", "gridFrequency", "Hz"),
    "output_voltage_v": ("loadInfo", "outputVoltage", "V"),
    "output_current_a": ("loadInfo", "outputCurrent", "A"),
    "output_frequency_hz": ("loadInfo", "outputFrequency", "Hz"),
    "load_percent": ("loadInfo", "loadRate", "%"),
    "battery_voltage_v": ("batInfo", "batVoltage", "V"),
    "battery_current_reported_a": ("batInfo", "batCurrent", "A"),
}
DETAIL = {
    "inverter_temperature_c": ("INV Temperature", "°C"),
    "pv_temperature_c": ("PV Temperature", "°C"),
    "ambient_temperature_c": ("Ambient Temperature", "°C"),
    "charger_temperature_c": ("CHG Temperature", "°C"),
    "load_energy_today_kwh": ("Daily Power Consumption", "kWh"),
    "load_energy_total_kwh": ("Cumulative Load Power Consumption", "kWh"),
    "grid_import_energy_total_kwh": ("Cumulative Energy Bought from Grid", "kWh"),
    "grid_export_energy_total_kwh": ("Cumulative Energy Sold to Grid", "kWh"),
    "grid_import_energy_today_kwh": ("Daily Energy Bought from Grid", "kWh"),
    "grid_export_energy_today_kwh": ("Daily Energy Sold to Grid", "kWh"),
    "pv1_energy_today_kwh": ("PV1 Daily Power Generation", "kWh"),
    "pv2_energy_today_kwh": ("PV2 Daily Power Generation", "kWh"),
    "pv1_energy_total_kwh": ("PV1 Cumulative Power Generation", "kWh"),
    "pv2_energy_total_kwh": ("PV2 Cumulative Power Generation", "kWh"),
}


def normalize_summary(response):
    obj = object_payload(response, dict)
    popup = _map(obj.get("popupData"))
    result = {key: quantity(obj.get(field), unit) for key, (field, unit) in SUMMARY.items()}
    result.update({key: quantity(_map(popup.get(group)).get(field), unit)
                   for key, (group, field, unit) in POPUP.items()})
    bat = _map(popup.get("batInfo"))
    soc = _soc(bat.get("batSOC"))
    result["battery_soc_percent"] = soc if soc is not None else _soc(obj.get("batSoc"))
    power = result["battery_power_reported_w"]
    if power is None:
        power = quantity(bat.get("batPower"), "W")
        result["battery_power_reported_w"] = power
    mode = obj.get("batWorkMode")
    result["battery_work_mode"] = mode if type(mode) is int else None
    # Only -1/discharging has been corroborated on the live device.
    result["battery_direction"] = "discharging" if type(mode) is int and mode == -1 else None
    # Public convention: positive = discharge, negative = charge. Unknown mode
    # cannot be converted yet; reported value remains accessible.
    result["battery_power_w"] = abs(power) if power is not None and result["battery_direction"] == "discharging" else None
    online = obj.get("onlineStatus")
    result["online"] = bool(online) if type(online) is int and online in (0, 1) else None
    return result


def normalize_details(response):
    obj = object_payload(response, dict)
    # displayMap is a flat duplicate of deviceInfoMap for the observed device.
    fields = {}
    for group in _map(obj.get("deviceInfoMap")).values():
        fields.update(_map(group))
    fields.update(_map(obj.get("displayMap")))
    result = {key: quantity(fields.get(field), unit) for key, (field, unit) in DETAIL.items()}
    result.update({
        "model": _text(fields.get("Device Model")),
        "firmware": _text(fields.get("Device Firmware 1 Version")),
        "operating_mode": _text(fields.get("State Machine Status")),
        "battery_status": _text(fields.get("Energy Terminal-Battery Status")),
        "bms_status": _text(fields.get("BMS Connection Status")),
        "grid_status": _text(fields.get("Energy Terminal-Mains Status")),
        "detail_battery_soc_percent": _soc(fields.get("Battery Capacity Percentage")),
    })
    return result


def normalize(summary, details):
    """Merge separately sampled endpoints without replacing summary SOC."""
    return normalize_summary(summary) | normalize_details(details)
