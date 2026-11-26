# Observed schema and normalized snapshot

Validated against a successful live capture from SP100-12000-48 on 2026-11-26.
The four read endpoints return result=0, obj (list for discovery, object for
telemetry), data=null and taskResult=null. The new fixture is a synthetic,
minimal subset of that observed schema with invented IDs and measurement values;
no original capture or private metadata is committed.

Discovery: plant obj[].id; control obj[].deviceSn (inverter), datalogSn
(logger). Do not use logger serial for device details. Both telemetry calls
used the inverter serial on the observed installation. Other models and mixed
device installations are unverified. Discovery does not guess a device when
more than one matches. Use --plant-id and --inverter-sn to disambiguate.

Summary: use obj.popupData and obj.batSoc; never use deviceDetailInfoVo.soc
because it returned a false default zero. A genuine zero is preserved;
missing, --, invalid quantities and unknown units become null. Detail uses
deviceInfoMap and displayMap. The two endpoints are sampled separately,
not atomically. Do not infer freshness from onlineStatus alone.

Battery convention in the normalized output: positive discharge, negative
charge. Only batWorkMode=-1 has been corroborated by devicePowerWorkModeText
"Discharging" and battery status. Other modes yield null signed power until
validated; the original reported numeric power and mode are retained.
Grid power remains reported, not signed import/export. Energy counters
are reported as kWh, not calculated from instantaneous power.

Historical chartMap has twelve channels, keys in chartMap.key and units in
chartMap.unit (mapping by channel name), each with HH:MM keys spaced five
minutes apart. Null samples are missing, not zero. Negative and positive
battery samples exist; the historic sign convention, query date/timezone
semantics and backfill still need verification. No historical MQTT publishing
yet.

Local snapshot (does not query the API):
```sh
python -m aec_cloud_gateway.snapshot --capture captures/example.json
```
One-shot live snapshot (prompts for credentials unless environment set):
```sh
python -m aec_cloud_gateway.snapshot
```
Only the observed model is validated; unknown models may have absent or
different fields. No daemon, control features, or broker integration yet.
