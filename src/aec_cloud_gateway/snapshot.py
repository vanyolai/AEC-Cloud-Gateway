"""Read a single normalized snapshot, from live API or private capture."""
import argparse
import getpass
import json
import os
import sys
from datetime import date
from pathlib import Path

from .client import AECClient
from .discovery import discover, select_inverter
from .errors import AECError
from .telemetry import normalize


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", type=Path, help="Read an existing local diagnostic capture")
    p.add_argument("--plant-id")
    p.add_argument("--inverter-sn")
    args = p.parse_args(argv)
    try:
        if args.capture:
            if args.plant_id or args.inverter_sn:
                p.error("Selection options are only for live queries")
            with args.capture.open(encoding="utf-8") as f:
                data = json.load(f)
            result = normalize(data["get_home_count_data"], data["get_device_by_sn"])
        else:
            email = os.environ.get("AEC_EMAIL") or input("AEC email: ").strip()
            password = os.environ.get("AEC_PASSWORD") or getpass.getpass("AEC password: ")
            with AECClient(email, password) as client:
                device = select_inverter(discover(client, args.plant_id), args.inverter_sn)
                # Both reads used deviceSn for the observed inverter; never use datalogSn.
                summary = client.get_home_count_data(device.plant_id, device.serial)
                details = client.get_device_by_sn(device.serial, date.today())
                result = normalize(summary, details)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (AECError, ValueError, KeyError, TypeError, OSError, EOFError) as exc:
        print(f"Snapshot failed: {type(exc).__name__}", file=sys.stderr)
        if isinstance(exc, AECError):
            print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
