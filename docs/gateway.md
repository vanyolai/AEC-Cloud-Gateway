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

Consumers must require both gateway and corresponding source availability,
and reject stale fetched_at timestamps using their own freshness threshold.
This timestamp is the UTC time of local fetch/publication, not inverter sample
time. Retained state is intentionally not replaced by zero on a failure.
The two payloads are separately sampled. Null fields remain JSON null.
The gateway marks source availability offline on reconnect before resuming
polls. A failure backs off per source; detail polling is suspended if the
summary fails or the inverter is offline. Authentication errors and exhausted
rate-limit errors suspend polling until a manual restart, to avoid hammering
the undocumented API. MQTT outage pauses cloud polling. There are no MQTT
subscriptions or control commands.

See deploy/compose.yaml and deploy/gateway.env.example. The initial published
image targets linux/amd64. CI builds the image; broker and live deployment
must still be validated separately.
