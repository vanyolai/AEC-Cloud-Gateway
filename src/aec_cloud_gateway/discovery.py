"""Discovery for the observed AEC obj envelope."""
from dataclasses import dataclass

from .errors import ProtocolError


def object_payload(response, expected):
    if not isinstance(response, dict) or type(response.get("result")) is not int or response["result"] != 0:
        raise ProtocolError("Expected successful AEC response")
    obj = response.get("obj")
    if not isinstance(obj, expected):
        raise ProtocolError("Unexpected AEC obj shape")
    return obj


@dataclass(frozen=True)
class Inverter:
    plant_id: str
    serial: str
    logger_serial: str | None


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ProtocolError("Invalid discovery identifier")
    value = str(value)
    if not value or not value.isascii() or not all(c.isalnum() or c in "-_" for c in value):
        raise ProtocolError("Invalid discovery identifier")
    return value


def parse_devices(plant_id, response):
    plant_id = _id(plant_id)
    devices = []
    for row in object_payload(response, list):
        if not isinstance(row, dict):
            raise ProtocolError("Invalid device record")
        if row.get("plantId") is not None and _id(row["plantId"]) != plant_id:
            raise ProtocolError("Device belongs to another plant")
        serial = _id(row.get("deviceSn"))
        logger = row.get("datalogSn")
        devices.append(Inverter(plant_id, serial, _id(logger) if logger is not None else None))
    return devices


def discover(client, plant_id=None):
    plants = object_payload(client.get_plants(), list)
    requested = _id(plant_id) if plant_id is not None else None
    devices = []
    seen = set()
    for row in plants:
        if not isinstance(row, dict):
            raise ProtocolError("Invalid plant record")
        pid = _id(row.get("id"))
        if pid in seen:
            raise ProtocolError("Duplicate plant ID")
        seen.add(pid)
        if requested is None or requested == pid:
            devices.extend(parse_devices(pid, client.get_home_control_sn(pid)))
    if requested is not None and requested not in seen:
        raise ProtocolError("Requested plant not found")
    return devices


def select_inverter(devices, serial=None):
    selected = [d for d in devices if serial is None or d.serial == _id(serial)]
    if len(selected) != 1:
        raise ProtocolError("Expected exactly one matching device; specify plant and inverter")
    return selected[0]
