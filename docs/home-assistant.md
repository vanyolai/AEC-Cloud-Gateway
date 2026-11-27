# Home Assistant MQTT Discovery (v0.5.0, experimental)

Optional read-only discovery using the existing summary/detail topics.
No HA dependency, new cloud calls, subscriptions, credentials, or commands.
The default remains disabled. References:
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/),
[MQTT sensor](https://www.home-assistant.io/integrations/sensor.mqtt/).

## Enable

Connect Home Assistant's MQTT integration to the same broker. In the gateway's
private .env:

    MQTT_ENABLED=true
    HA_DISCOVERY_ENABLED=true
    HA_DISCOVERY_PREFIX=homeassistant
    HA_DEVICE_NAME=AEC Inverter

Optional HA_DEVICE_ID overrides the stable ID derived from MQTT_TOPIC_PREFIX
(SHA-256 prefix, first 20 hex characters, prefixed aec_). It is an installation
identity, not the inverter serial: preserve it when moving topics, or choose
a distinct ID per inverter. Never reuse an ID for different devices. Changing
the discovery prefix or ID requires removing old retained configs first.

Grant the gateway MQTT account write access to its own telemetry prefix and
homeassistant/sensor/<device_id>/+/config (adapt the prefix and node ID).
For example, with explicit HA_DEVICE_ID=aec_proteus:

    user aec-gateway
    topic write aec/inverter/#
    topic write homeassistant/sensor/aec_proteus/+/config

Merge these rules into an existing ACL rather than replacing it. HA needs
read permissions to discovery and telemetry topics. MQTT v3.1.1 PUBACK does
not prove ACL acceptance; verify the actual retained topics with an authorized
subscriber and Home Assistant, especially if the broker silently drops writes.

## Entities and state

One logical device, 50 sensor configurations covering normalized summary and
detail fields (except online, represented through availability). Config topics
use <discovery_prefix>/sensor/<device_id>/<field>/config; unique IDs combine
device ID and field. Device name can change without changing identity.
Configurations are retained QoS 1, published one at a time on connection,
then replayed on reconnect. Broker persistence is recommended. There is no
HA birth-topic subscription. A failed config publication is retried after 60s
independently of the cloud scheduler. The synchronous MQTT ACK timeout may
still delay a polling cycle by up to its timeout. Diagnostics home_assistant
shows config progress and errors; completion means the broker acknowledged
publication, not that HA accepted the schema.

Each sensor references the original state JSON and both gateway and source
availability (all required online). Expire_after is three times the associated
polling interval. Because state is retained for existing consumers, HA replay
can temporarily reset the expiration timer; expire_after is not proof of
measurement freshness. Existing consumers should check fetched_at separately.
Unknown numeric values are rendered as None/unknown, never coerced to zero.
Model, firmware, raw battery mode and secondary SOC are diagnostic and disabled
by default. Cumulative energy uses energy/kWh/total_increasing; daily/monthly/
yearly energy counters are exposed without a statistics state class pending
reset behavior validation. Instantaneous numeric values use measurement.
Only confirmed discharge is normalized to positive battery power; charge
and grid direction remain unverified. Use cumulative grid import/export and
PV energy for HA Energy dashboard only after validating meter monotonicity;
do not derive import/export from reported grid power.

## Validate

Check container logs for HA discovery published: 50 configs and diagnostics
home_assistant.complete=true. Verify retained config and state with MQTTX
or another authorized MQTT subscriber. In HA Settings > Devices & services >
MQTT, check the device, representative power/SOC/energy sensors, null values,
availability during gateway stop, stable entity IDs on restart and reconnect.
Basic live HA validation succeeded on 2026-11-27 after setting up HA's MQTT
integration with a dedicated broker account. The device and representative
summary/detail sensors appeared, with units and unknown battery energy values
preserved. If configs are acknowledged but no device appears, verify that HA
is authenticated to the same broker and uses the same discovery prefix before
changing gateway code. Full entity count, reconnect, availability/expiration,
removal and Energy dashboard behavior remain to be tested.

## Remove

To remove this version's retained discovery configs without changing telemetry,
keep the same discovery prefix, device ID and MQTT topic prefix, set
HA_DISCOVERY_ENABLED=false and HA_DISCOVERY_REMOVE=true, restart and wait for
the removal-complete log. Empty retained configs are sent for all 50 topics.
Verify in HA and the broker, then set HA_DISCOVERY_REMOVE=false. Merely disabling
discovery does not delete retained configs. For older/renamed entities, remove
their exact old config topics separately; never clear the entire HA namespace.
