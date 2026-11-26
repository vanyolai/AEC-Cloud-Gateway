"""One-shot, read-only live API smoke test; not an unattended poller."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .client import AECClient
from .errors import AECError

# Fixed field names only: arbitrary API keys and values may contain identifiers.
KNOWN_FIELDS = (
    "result", "obj", "data", "popupData", "deviceDetail", "deviceDetailInfoVo",
    "deviceInfoMap", "detailInfoMap", "displayMap", "chartMap", "key", "unit",
)


def describe(payload: Any) -> dict[str, Any]:
    """Report shapes, not arbitrary field names, values or identifiers."""
    found: dict[str, set[str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in KNOWN_FIELDS:
                    found.setdefault(key, set()).add(type(child).__name__)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return {"root": type(payload).__name__,
            "fields": {key: sorted(types) for key, types in sorted(found.items())}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, default=date.today(),
                        help="Inverter query date (default: local today)")
    parser.add_argument("--show-private-discovery", action="store_true",
                        help="Print raw plant/control responses locally; never share them")
    parser.add_argument("--capture", type=Path,
                        help="Create a private JSON capture (new file, mode 0600)")
    args = parser.parse_args(argv)
    capture = None
    if args.capture is not None:
        try:
            fd = os.open(args.capture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            capture = os.fdopen(fd, "w", encoding="utf-8")
        except OSError:
            print("Cannot create capture: choose a new file in an existing directory.", file=sys.stderr)
            return 2

    responses: dict[str, Any] = {}
    stage = "credentials"
    status = 0

    def record(name: str, payload: dict[str, Any], *, discovery: bool = False) -> None:
        responses[name] = payload
        print(f"{name}: {json.dumps(describe(payload), sort_keys=True)}")
        if discovery and args.show_private_discovery:
            print("PRIVATE discovery response — do not share:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        email = os.environ.get("AEC_EMAIL") or input("AEC email: ").strip()
        password = os.environ.get("AEC_PASSWORD") or getpass.getpass("AEC password: ")
        stage = "login"
        with AECClient(email, password) as client:
            client.login()
            print("login: OK (session cookie established)")
            stage = "get_plants"
            record(stage, client.get_plants(), discovery=True)
            stage = "plant_id"
            plant_id = input("Plant ID: ").strip()
            stage = "get_home_control_sn"
            record(stage, client.get_home_control_sn(plant_id), discovery=True)
            stage = "device_ids"
            device_sn = input("Summary device serial (from control response): ").strip()
            inverter_sn = input("Inverter serial (NOT logger; Enter if same as summary): ").strip() or device_sn
            stage = "get_home_count_data"
            record(stage, client.get_home_count_data(plant_id, device_sn))
            stage = "get_device_by_sn"
            record(stage, client.get_device_by_sn(inverter_sn, args.day))
        print("Smoke test: OK (response shapes observed; semantics not validated)")
    except (AECError, ValueError, EOFError) as exc:
        # Do not print server bodies, arbitrary exception messages or identifiers.
        print(f"Smoke test failed at {stage}: {type(exc).__name__}", file=sys.stderr)
        status = 1
    except KeyboardInterrupt:
        print(f"Smoke test cancelled at {stage}", file=sys.stderr)
        status = 130
    finally:
        if capture is not None:
            try:
                with capture:
                    json.dump(responses, capture, indent=2, ensure_ascii=False)
                    capture.write("\n")
                print("Private capture saved; review/redact locally before sharing.")
            except OSError:
                print("Capture write failed; file may be incomplete.", file=sys.stderr)
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
