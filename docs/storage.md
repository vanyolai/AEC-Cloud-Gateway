# Optional PostgreSQL storage (v0.4.0)

Both outputs receive the same normalized summary/detail values and UTC fetch
timestamp. Only successfully fetched online snapshots are stored. No raw AEC
responses or credentials are stored. Enable at least one output. MQTT remains
enabled by default; PostgreSQL is opt-in. Outputs run sequentially with
connect/statement/publish timeouts and SQL failure backoff, not separate
workers. There is no durable spool or replay yet: snapshots can be lost
during an outage. MQTT retains the latest published state, not history.

## Existing PostgreSQL

Create a dedicated login and database owned by it (as database administrator):

    CREATE ROLE aec_gateway LOGIN;
    \password aec_gateway
    CREATE DATABASE aec_gateway OWNER aec_gateway;

The password command is a psql meta-command; use interactive psql.
Do not grant superuser privileges. Set DB_ENABLED=true, DB_HOST, DB_NAME,
DB_USER and DB_PASSWORD in .env. Use DB_SSLMODE=verify-full with a suitable CA
and hostname on untrusted networks. Prefer is appropriate on trusted networks
only. The gateway must be able to resolve and connect to the database host.
Schema migration 1 runs automatically, transactionally with an advisory lock.
Protect backups and access: plant and inverter identifiers are private.

For a bundled instance, combine deploy/compose.yaml with
deploy/compose.postgresql.yaml and set DB_HOST=aec-db. PostgreSQL is not
published to the host and uses a named persistent volume. Base Compose uses
the external cloud network; create it or adapt networking for your setup.
The bundled example uses the initial DB_USER as its bootstrap administrator;
for stronger isolation, provision a restricted application login separately.

## Querying

    SELECT fetched_at, (payload->>'pv_power_w')::double precision AS pv_w,
           (payload->>'battery_soc_percent')::double precision AS soc
    FROM aec_snapshots
    WHERE source = 'summary'
    ORDER BY fetched_at DESC LIMIT 20;

One row per source fetch. JSONB preserves null and zero distinctly. The
primary key includes plant, inverter, source and fetch time; retrying the
same event is idempotent. Fetch time is not inverter measurement time.
Historical chart ingestion, timezone validation, backfill, typed channel
views, durable output buffering and REST export are later milestones.
DB_RETENTION_DAYS=0 keeps snapshots indefinitely. A positive value triggers
hourly bounded deletion batches for the selected inverter on successful
writes. Cleanup may lag after downtime. Back up the database and monitor
disk usage.
