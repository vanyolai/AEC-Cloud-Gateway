# MQTT gateway (experimental)

The service is read-only and uses one selected inverter per instance. Set
AEC_PLANT_ID and AEC_INVERTER_SN when discovery is ambiguous. Use a unique
MQTT_CLIENT_ID and MQTT_TOPIC_PREFIX for every instance. The default prefix
is aec/inverter; the inverter serial can be used as a local suffix if desired.

The compose example connects to an existing broker on an external cloud network.
No MQTT broker is bundled, no inbound ports are required. Use a restricted
MQTT account with write access only to the chosen prefix; TLS is supported
with the system CA trust store. Plain MQTT is for a trusted private network.
Credentials belong in a private env file, never in the repository. Docker
administrators can inspect environment variables.

Before enabling continuous polling, check provider terms and rate limits;
these remain unverified. AEC_POLL_ENABLED defaults to false. The provisional
minimum intervals are 60 seconds for summary and 300 for detail. AEC_TIMEZONE
controls the date used for detail requests; it does not resolve historical
timestamp semantics. No historical backfill, Home Assistant Discovery or
MyHome integration is included yet.

## MQTT schema 1

All topics are under MQTT_TOPIC_PREFIX. Each message is retained at QoS 1.

| Topic suffix | Payload |
| --- | --- |
| availability | online/offline (gateway MQTT connection; Last Will offline) |
| summary/availability | online/offline (successful online inverter summary) |
| summary/state | normalized summary JSON, schema, fetched_at |
| detail/availability | online/offline (successful detail, valid summary) |
| detail/state | normalized detail JSON, schema, fetched_at |
| diagnostics | gateway health JSON, independent schema 1 and reported_at |

Consumers must require both gateway and corresponding source availability,
and reject stale fetched_at timestamps using their own freshness threshold.
This timestamp is the UTC time immediately after the local fetch, not inverter sample
time. Retained state is intentionally not replaced by zero on a failure.
The two payloads are separately sampled. Null fields remain JSON null.
The gateway marks source availability offline on reconnect before publishing new state. A failure backs off per source; detail polling is suspended if the
summary fails or the inverter is offline. Authentication errors and exhausted
rate-limit errors suspend polling until a manual restart, to avoid hammering
the undocumented API. MQTT and PostgreSQL outputs are independently optional; neither gates cloud polling. An output outage can lose samples for that output; there is no durable retry spool yet. There are no MQTT
subscriptions or control commands.

See deploy/compose.yaml and deploy/gateway.env.example. Copy the example to a private .env beside the Compose file; Dockge can manage it directly. See [PostgreSQL storage](storage.md) for optional storage. The initial published
image targets linux/amd64. CI builds the image; broker and live deployment
must still be validated separately.

## Gateway diagnostics (v0.4.1)

AEC_DIAGNOSTICS_INTERVAL defaults to 60 seconds (0 disables periodic reports).
Reports use the existing MQTT prefix, QoS 1 and retain. The same JSON is
logged locally, including in database-only mode or when MQTT is disconnected.
Reporting continues while cloud polling is halted; it issues no extra API
requests and does not change the polling intervals or API retry policy.
Reports are periodic rather than real-time; consumers must check reported_at
freshness and gateway availability. Counters are process-local and reset on
restart. started_at identifies the reporting process lifetime.

Fields: schema, version, reported_at, started_at, uptime_seconds,
polling_halted, device_selected. The api object counts HTTP request attempts
(including login), retry attempts and successful logins/session renewals.
Discovery, summary and detail have separate success/failure counts,
consecutive failures, offline polls (a subset of failed polls), last success
and error timestamps, last error, last and mean duration in milliseconds,
blocked and next_poll_in_seconds (null while blocked). Durations include
the complete logical call, including retries and any necessary login.
Discovery is blocked after device selection. Detail is blocked without a
successful online summary. Last errors remain visible after recovery;
consecutive_failures resets on success.

The mqtt object reports enabled, connected, connections, connection_errors,
published (fully acknowledged state and availability), skipped (broker
offline), publish_errors (including availability/sync failures),
diagnostic_errors and last_success_at. The postgresql object reports enabled,
connected (last known open connection, not an active probe), stored
(acknowledged inserts including idempotent duplicates), write_errors
(uncertain or failed writes), skipped (backoff), cleanup_errors,
last_success_at and retry_in_seconds. A disabled output reports enabled=false.
No credentials, identifiers, response bodies or arbitrary exception strings
are included. Network timeout, connection failure, TLS and HTTP status errors
are distinguished when the client can determine them.

Diagnostic logging and MQTT heartbeat failures do not change source
availability or trigger API retries. Delivery failures remain best-effort;
a durable replay queue is not implemented.
