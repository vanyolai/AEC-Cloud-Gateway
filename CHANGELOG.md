# Changelog

## 0.5.0 — 2026-11-27
- Optional Home Assistant MQTT discovery: 50 sensors, stable installation IDs,
  retained config, dual availability, expiration and explicit removal mode.
- Bounded independent discovery retries and diagnostic progress.
- Developer handover and deployment/test documentation.
- Existing MQTT and PostgreSQL contracts unchanged. Live HA test pending.

## 0.4.2 — 2026-11-27
- Session rejection reason and endpoint, cookie metadata/presence/rotation,
  observed session ages; no secret values.
- Independent MQTT and log heartbeat intervals.
- CI and Docker build succeeded; live session renewal observed.

## 0.4.1 — 2026-11-27
- Runtime diagnostics, output counts, safe transport errors and MQTT heartbeat.
- Live collection verified. CI for this commit failed; subsequent v0.4.2 CI passed.

## 0.4.0 — 2026-11-26
- Optional PostgreSQL storage, transactional schema v1 and retention.
- Independent optional MQTT and database outputs; no durable spool.
- CI/Docker and overnight live collection verified.

## 0.3.0 — 2026-11-26
- Read-only Docker MQTT daemon, separate schedules, retained state and availability.

## 0.2.0 — 2026-11-26
- Device discovery and normalized snapshots; null/zero preservation.

## 0.1.1 — 2026-11-26
- Interactive smoke test and private capture/replay workflow.

## 0.1.0 — 2026-11-26
- Standalone Python client, synthetic fixtures, retry and session renewal, CI.
