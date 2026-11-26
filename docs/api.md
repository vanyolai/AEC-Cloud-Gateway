# Observed AEC Cloud API

Base origin: `https://monitor.ai-ec.cloud:8443`.
Observation date: 2026-11-26. Hardware: SP100-12000-48, inverter firmware
V1.0.58, datalogger firmware 2.0.6.1.9. This is an undocumented mobile API,
not a vendor contract. No credentials or actual device identifiers are stored here.

| Method | Path | Inputs |
| --- | --- | --- |
| POST | `/user/login` | JSON: email, MD5 password, phoneOs=1, phoneModel="1.1", appVersion="V1.1" |
| GET | `/plant/getPlantVos` | None |
| GET | `/energy/getHomeControlSn/{plantId}` | Plant ID in path |
| POST | `/energy/getHomeCountData` | Query: plantId, deviceSn |
| POST | `/device/getDeviceBySn` | Query: deviceType=55, time=YYYY-MM-DD, sn (inverter serial) |

Login success was observed as integer `result=0` and a `JSESSIONID` cookie.
Keep the cookie in memory; do not log or persist it. The known application-level
login-required text is `Please login`. HTTP 401 is also treated as a renewal
signal. HTTP 403 is not retried automatically. Other session expiry signals
have not been validated. Nonzero application result codes are not assumed to
mean expiry. A successful read may have a different envelope; no parser is
imposed on its data. HTTP POST on the two telemetry routes is used for reading,
not configuration changes.

## Observed telemetry

- `getHomeCountData`: `popupData` and energy counters.
- `getDeviceBySn`: `deviceInfoMap`, `displayMap`, `chartMap.key` and
  `chartMap.unit`; 12 historical channels at five-minute resolution observed.
- `deviceDetail` can be null. `deviceDetailInfoVo` can contain placeholder
  zeros (SOC 0 while the actual SOC was 24). Do not treat these as authoritative
  without corroboration. JSON null, numeric zero and missing values differ.
- The battery power sign convention, historical backfill for earlier days,
  timezone interpretation, exact chart schema and alarm-code endpoint are
  not yet verified. Do not invent normalized mappings for these.

## Retry and security policy

Default timeout: 20 seconds per request (requests connect/read timeout, not a
whole-operation deadline). Default maximum: two transient retries per request,
with jitter and up to 30 seconds of backoff. The client respects Retry-After
when within that bound, otherwise it raises RateLimitError rather than sending
an earlier retry. A read can perform one session renewal; the renewal has its
own retry budget. Callers should back off between failed polling cycles.

No redirects, TLS verification bypass, credential logging or inverter write
methods. MD5 is solely a compatibility measure for the vendor login protocol,
not a recommended password hashing algorithm. Never reuse the digest elsewhere.

Before running continuous polling, check the vendor's applicable terms,
API access restrictions and rate limits. The preliminary polling intervals
of 60 and 300 seconds are configuration defaults for the future gateway,
not documented provider limits.
