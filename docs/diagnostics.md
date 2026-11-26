# Live diagnostics

Run once, from an interactive terminal, after installing the package:

```sh
mkdir -m 700 -p captures
python -m aec_cloud_gateway.diagnostics --show-private-discovery \
  --capture "captures/aec-smoke-$(date +%Y%m%d-%H%M%S).json"
```

Enter the email and password locally (or use AEC_EMAIL/AEC_PASSWORD). The
password prompt is hidden. Select the plant ID and summary device serial
from the discovery responses. Enter the inverter serial separately if it
differs; do not use the datalogger serial for inverter details.

The flag `--show-private-discovery` displays raw discovery responses in the
local terminal, including possible account information. Without that flag,
only an allowlisted structural summary is printed. Input is visible in the
terminal except the password; do not share a complete terminal transcript.
Use `--day YYYY-MM-DD` to query another date explicitly; the default is local
today. Historical availability and timezone semantics remain unverified.

The optional capture contains raw successful read responses, including any
sensitive information returned by the API, but no login response or cookie
headers. The file is created exclusively with mode 0600 and is not overwritten.
Keep it in an ignored `captures/` directory with mode 0700, and inspect and
redact it locally before sharing anything. Never commit raw captures. Partial
successful responses are preserved if a later step fails.

A successful run confirms request/response transport, not the meaning of SOC,
power signs, energy units or the completeness of telemetry. This command is a
one-shot diagnostic, not a polling daemon. Automatic ID selection is deferred
until the discovery response schemas have been validated.
