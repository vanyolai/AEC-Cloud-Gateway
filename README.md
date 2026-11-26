# AEC Cloud Gateway

Unofficial, self-hosted integration for AEC Cloud inverter telemetry. The first
milestone is a standalone Python client, with no MQTT or Home Assistant dependency.
This is an independent project, not a Sunpura fork or a vendor-supported SDK.

**Status:** 0.4.0 development baseline. The endpoints were tested manually on
2026-11-26 against an SP100-12000-48 inverter. This implementation has offline
unit tests; the client has now been exercised successfully against a live SP100-12000-48 account.

## Installation

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

## Usage

Set `AEC_EMAIL` and `AEC_PASSWORD` in the process environment. The password is
provided in plaintext to the client, which computes the MD5 representation
required by the observed mobile API. Treat both password and digest as secrets.
For a local interactive session (Bash):

```sh
read -r -p 'AEC email: ' AEC_EMAIL
read -r -s -p 'AEC password: ' AEC_PASSWORD; printf '\n'
export AEC_EMAIL AEC_PASSWORD
```

```python
from datetime import date
from aec_cloud_gateway import AECClient

with AECClient.from_env() as client:
    plants = client.get_plants()
    # Inspect the private response locally to obtain the plant ID.
    # Use the inverter serial, not the datalogger serial, for device details.
    plant_id = "REPLACE_WITH_PLANT_ID"
    inverter_sn = "REPLACE_WITH_INVERTER_SN"
    control = client.get_home_control_sn(plant_id)
    summary = client.get_home_count_data(plant_id, inverter_sn)
    details = client.get_device_by_sn(inverter_sn, date.today())
```

Select the actual control device serial for `get_home_count_data` using the
plant/control response; do not assume that every installation uses the same ID
for both calls. `get_device_by_sn` must receive the inverter serial.
Raw responses may contain private identifiers and production telemetry. Do not
paste them into public issues or commit them. To check response shape without
printing values, use `print(sorted(plants))`, for example.

Login is lazy, with a reusable cookie session. The client renews an expired
session once per read and applies bounded exponential backoff with jitter for
transient network failures, HTTP 429 and selected 5xx errors. It does not follow
redirects or disable TLS verification. It is synchronous and not thread-safe;
use one instance per worker. No inverter control or write methods are exposed.

## API coverage

See [API notes](docs/api.md) for observed endpoints, limitations and security
considerations. Responses are returned as dictionaries without flattening or
assuming undocumented field semantics. The included fixtures are synthetic
subsets of observed structures, not exact sanitized captures.

## Roadmap

1. Standalone client and offline CI (this milestone); live smoke test and exact
   sanitized fixtures before declaring API compatibility.
2. Normalized telemetry model after validation of fields, units and signs.
3. Docker service and MQTT publication to an existing broker. Configurable
   summary polling (60 seconds) and detail polling (300 seconds).
4. Optional PostgreSQL snapshot storage and independent MQTT output.
5. Home Assistant MQTT Discovery, stable identities and availability handling.
6. Historical backfill and REST export. MyHome consumes MQTT via a separate gateway.

No control features are planned in the initial milestones. Do not enable
unattended polling until the vendor's applicable terms and rate limits have
been reviewed. There is no guarantee that the undocumented API will remain
compatible.

## Development

Run `python -m pytest` and `python -m build`. CI runs these on Linux for Python
3.11–3.14, without real credentials or live network requests in the tests.

License selection is pending; no third-party source code has been copied.

## Live smoke test

See [live diagnostics](docs/diagnostics.md) for the one-shot interactive test
and private capture workflow. The four read endpoints were validated successfully on 2026-11-26.

## Normalized snapshot

See [telemetry](docs/telemetry.md) for automated discovery, normalized fields,
private capture replay and remaining validation limitations.

## Experimental Docker MQTT gateway

See [gateway documentation](docs/gateway.md) and the [Compose example](deploy/compose.yaml).
Continuous polling is disabled until explicitly enabled after provider terms review.

See [PostgreSQL storage](docs/storage.md) for optional local history and external or bundled database configuration.
