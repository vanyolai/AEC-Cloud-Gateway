# Development handover / resume here

Updated 2026-11-27. Repository: https://github.com/vanyolai/AEC-Cloud-Gateway.
This is a standalone, unofficial read-only gateway, not a Sunpura fork.
Start a new development conversation by reading this file, CHANGELOG.md,
README.md, docs/api.md, docs/telemetry.md, docs/gateway.md and the current
main branch. Always check current HEAD and CI before changing code.
This document records the v0.5.0 implementation baseline, not a promise
that its CI or live HA test has passed.

## Architecture and boundaries

AECClient (client.py) owns one requests.Session, HTTPS verification, bounded
retries and at most one re-login per logical read. discovery.py selects a
plant/inverter; telemetry.py normalizes observed payloads. snapshot.py is a
one-shot CLI, diagnostics.py captures private raw data. gateway.py runs
sequential summary/detail polling and independent optional outputs.
storage.py owns PostgreSQL schema/migrations and backoff; health.py and
session_health.py report process-local operational counters; ha_discovery.py
generates retained HA config independently of polling. No API control writes,
no MQTT subscriptions. HA is optional, MyHome is not a dependency.
MyHome uses a separate myhome-mqtt-gateway project consuming the existing
MQTT schema. Keep generic integration in this repository.

## Validated AEC interface

Origin https://monitor.ai-ec.cloud:8443. Observed SP100-12000-48 firmware
V1.0.58, datalogger 2.0.6.1.9. POST /user/login JSON email, MD5 password,
phoneOs=1, phoneModel="1.1", appVersion="V1.1", success result=0 plus
JSESSIONID. GET /plant/getPlantVos -> obj[].id. GET
/energy/getHomeControlSn/{plantId} -> obj[].deviceSn (inverter, not datalogSn).
POST /energy/getHomeCountData query plantId/deviceSn, POST /device/getDeviceBySn
query deviceType=55,time=YYYY-MM-DD,sn=inverter serial. Both read-only in
observed use. obj contains payload; data can be null. Do not assume vendor
support, documented rate limits or compatibility with other models.
Never commit credentials, tokens, real IDs, raw captures or private logs.
Use synthetic fixtures. Source of truth for key mappings is telemetry.py.

Summary SOC is popupData.batInfo.batSOC or obj.batSoc. deviceDetailInfoVo
can return placeholder zero; do not use it. Preserve zero vs null.
Battery mode -1/discharging confirmed; normalized positive discharge, negative
charge convention, but charging mode is not validated. Other modes give null
signed power; reported values retained. Grid power sign not validated.
Historical chartMap: 12 channels, chartMap.key/unit, 5-minute HH:MM keys,
nulls. Historic dates, timezone, DST, meanings and alarm endpoint unverified.

## Deployment / operator baseline

One inverter per container. Docker python:3.13-slim, UID:GID 1000:1000,
read-only root filesystem, external cloud network. GHCR
ghcr.io/vanyolai/aec-cloud-gateway:<version>, linux/amd64. Docker workflow
also publishes sha-<commit>. Source checkout and Docker stack are separate.
Reference homelab uses Dockge with .env beside compose and env_file: .env;
external Mosquitto and PostgreSQL services. Do not commit actual installation
paths, host credentials or private config. See deploy examples. Existing
database role/database aec_gateway may be reused without migrations beyond v1.
DB_RETENTION_DAYS=0 is indefinite. MQTT_ENABLED=true and DB_ENABLED=true
can coexist; at least one output required. AEC_POLL_ENABLED=false by default
until operator reviews applicable terms. Summary 60s, detail 300s minimum.
AEC_TIMEZONE controls query date, not proven chart timezone. Cloud cycle
backoff starts at 300s, exponential to 3600s or configured interval if greater.
Authentication/rate limit errors halt polling until restart.
Use unique MQTT prefix and client ID per instance.

## Stable interfaces

MQTT prefix default aec/inverter. QoS1 retain: availability (gateway LWT),
summary/state, summary/availability, detail/state, detail/availability,
diagnostics. Both gateway and source availability required. State schema=1,
fetched_at UTC fetch timestamp (not device sample time). Diagnostics schema=1,
reported_at/started_at, uptime and counters reset on restart. Do not replace
null with zero. Do not rename keys or make MyHome-specific assumptions.
PostgreSQL aec_snapshots primary key plant_id,inverter_sn,source,fetched_at;
payload jsonb normalized values, schema_version=1. Separate fetch times,
no history replay. MQTT retains latest state, not history. Outputs run
sequentially; no guaranteed replay or nonblocking output workers.
HA discovery adds external config namespace only; disabled by default.

## Verified operational evidence (anonymized)

- 2026-11-26 live smoke test: all four endpoints and automatic discovery;
  normalized summary/detail live and offline capture replay succeeded.
- v0.4.0 overnight: 441 summary / 90 detail snapshots over ~7h35m at initial
  check; later table + indexes 656 KiB. Two transport failures recovered
  after 300s backoff, no fake samples. Observed max gaps summary 366.13s,
  detail 576.78s. Cause within TransportError not known.
- v0.4.1 live: 22/22 delivered both outputs in first 18min, no output errors;
  later 61 snapshots after ~50min, frequent successful renewals. Phone-off
  period still had 5 renewals in ~9min. Its CI failed (see below), not hidden.
- v0.4.2 live: three application login-required rejections with HTTP 200,
  outgoing JSESSIONID present, no HTTP 401, cookie set changed in same
  observation windows. Rejection ages 60.84/182.01/58.18s. Both telemetry
  endpoints affected. No proven cause; load balancer is only a hypothesis.
  Re-login worked. Do not change polling, session algorithm or TLS policy
  without further evidence. See docs/api.md.
- v0.4.2 verified green [CI](https://github.com/vanyolai/AEC-Cloud-Gateway/actions/runs/45936214277)
  and [Docker](https://github.com/vanyolai/AEC-Cloud-Gateway/actions/runs/45936214284).
  Baseline commit 709c55d82c90998eeaa2e93cec2b85659b57137d.
  v0.4.1 [CI failure](https://github.com/vanyolai/AEC-Cloud-Gateway/actions/runs/45930516732)
  was followed by green v0.4.2. Do not treat a successful Docker build as CI.
- v0.5.0 baseline commit 609804d6346a357a78369713e795e615ca6c6606:
  [CI](https://github.com/vanyolai/AEC-Cloud-Gateway/actions/runs/45937284827)
  and [Docker](https://github.com/vanyolai/AEC-Cloud-Gateway/actions/runs/45937284835)
  both verified successful. Operator deployed v0.5.0 on 2026-11-27:
  discovery and PostgreSQL initialization succeeded, summary/detail were
  stored and published, and all 50 HA configs were acknowledged in about
  50 seconds. Subsequent summary cycles continued normally. Actual retained
  delivery through the ACL and HA entity creation/state/availability/removal
  still require verification. No extra API calls. See docs/home-assistant.md.

## Next milestones / acceptance gates

1. v0.5.0: test discovery on a real broker/HA; verify ACL, retained configs,
   all entity IDs stable, state classes, null handling, restart, availability,
   remove mode and unchanged MyHome/DB behavior. Check diagnostic progress.
2. v0.6.0: investigate past-day getDeviceBySn using a bounded one-shot query
   (avoid parallel live pollers; check terms). Compare yesterday/today
   chartMap, channel units, timestamp semantics incl DST, nulls, duplicates,
   actual charge/grid signs. Build synthetic fixtures. Store chart data
   separately with explicit measurement timestamps and provenance, migration,
   idempotency and conservative rate-limited backfill. Do not backfill
   snapshot fetched_at with guessed measurement times. No uncontrolled bulk
   API access; validate a single day first.
3. v0.7.0: optional read-only REST API/dashboard backed by PostgreSQL, auth,
   pagination, bounds, UTC timestamps, access controls and privacy.
4. v0.8.0: durable bounded output queue, independent worker delivery and
   idempotency, retry/restart/outage tests; consider scheduling drift and
   reconnect behavior. No unbounded retries or data loss claims.
5. v1.0.0: compatibility matrix, security, upgrade/rollback, backup/restore,
   licensing decision, CI and live integration acceptance.

## Development and release checklist

Read actual code/tests, use synthetic data, keep the existing API contract.
Install editable dev dependencies: python -m pip install -e '.[dev]'.
Run python -m pytest and python -m build; CI covers 3.11–3.14 and disposable
PostgreSQL integration. Update pyproject version, compose image, Docker
workflow version tag, CHANGELOG and this handover plus relevant feature docs.
Current Docker workflow publishes independently of CI and uses a fixed tag
per release; future improvement: gate release publishing on green tests and
derive version tags safely. Confirm actual Actions outcomes and image
availability before operator upgrade; never claim local execution if only
remote CI ran. Use a new version for new code, not silently replacing older
version tags. Do not start live API tests with real secrets in CI.
On every milestone, update verified vs pending results in this document.
