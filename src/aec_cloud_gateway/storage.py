"""Optional PostgreSQL output for normalized snapshots."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

LOG = logging.getLogger("aec-gateway")


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    name: str
    user: str
    password: str = field(repr=False)
    sslmode: str = "prefer"
    retention_days: int = 0

    @classmethod
    def from_env(cls, env):
        for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
            if not env.get(key):
                raise ValueError(f"{key} is required when DB_ENABLED=true")
        try:
            port = int(env.get("DB_PORT", "5432"))
            retention = int(env.get("DB_RETENTION_DAYS", "0"))
        except ValueError:
            raise ValueError("Invalid DB_PORT or DB_RETENTION_DAYS") from None
        if not 1 <= port <= 65535 or not 0 <= retention <= 36500:
            raise ValueError("DB_PORT or DB_RETENTION_DAYS out of range")
        sslmode = env.get("DB_SSLMODE", "prefer")
        if sslmode not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
            raise ValueError("Invalid DB_SSLMODE")
        return cls(env["DB_HOST"], port, env["DB_NAME"], env["DB_USER"],
                   env["DB_PASSWORD"], sslmode, retention)


def migrate(conn):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(1862312401)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS aec_schema_migrations (
                version integer PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        versions = {row[0] for row in conn.execute("SELECT version FROM aec_schema_migrations")}
        if versions - {1}:
            raise ValueError("Unsupported database schema version")
        if 1 not in versions:
            conn.execute("""
                CREATE TABLE aec_snapshots (
                    plant_id text NOT NULL,
                    inverter_sn text NOT NULL,
                    source text NOT NULL CHECK (source IN ('summary', 'detail')),
                    fetched_at timestamptz NOT NULL,
                    schema_version integer NOT NULL,
                    payload jsonb NOT NULL,
                    PRIMARY KEY (plant_id, inverter_sn, source, fetched_at)
                )
            """)
            conn.execute("CREATE INDEX aec_snapshots_fetched_idx ON aec_snapshots (fetched_at)")
            conn.execute("INSERT INTO aec_schema_migrations (version) VALUES (1)")


class PostgresStorage:
    """Snapshot output with bounded retry backoff; no durable spool yet."""
    def __init__(self, config):
        import psycopg
        from psycopg.types.json import Jsonb

        self.config = config
        self.driver = psycopg
        self.jsonb = Jsonb
        self.conn = None
        self.retry_at = 0.0
        self.failures = 0
        self.cleanup_at = 0.0

    def connect(self):
        if self.conn is None or self.conn.closed:
            cfg = self.config
            self.conn = self.driver.connect(
                host=cfg.host, port=cfg.port, dbname=cfg.name,
                user=cfg.user, password=cfg.password, sslmode=cfg.sslmode,
                connect_timeout=5, autocommit=True,
                options="-c statement_timeout=5000 -c lock_timeout=3000",
                application_name="aec-cloud-gateway",
            )
            migrate(self.conn)
            LOG.info("PostgreSQL storage ready")

    def emit(self, device, source, values, fetched_at):
        self.connect()
        self.conn.execute("""
            INSERT INTO aec_snapshots
                (plant_id, inverter_sn, source, fetched_at, schema_version, payload)
            VALUES (%s, %s, %s, %s, 1, %s)
            ON CONFLICT (plant_id, inverter_sn, source, fetched_at) DO NOTHING
        """, (device.plant_id, device.serial, source, fetched_at, self.jsonb(values)))
        LOG.info("%s stored in PostgreSQL", source)
        if self.config.retention_days and time.monotonic() >= self.cleanup_at:
            self.conn.execute("""
                DELETE FROM aec_snapshots WHERE ctid IN (
                    SELECT ctid FROM aec_snapshots
                    WHERE plant_id = %s AND inverter_sn = %s
                    AND fetched_at < now() - (%s * interval '1 day')
                    LIMIT 10000
                )
            """, (device.plant_id, device.serial, self.config.retention_days))
            self.cleanup_at = time.monotonic() + 3600

    def attempt(self, action, *args):
        if time.monotonic() < self.retry_at:
            LOG.warning("PostgreSQL backoff; snapshot not stored")
            return
        try:
            getattr(self, action)(*args)
        except (self.driver.Error, ValueError) as exc:
            self.close()
            self.failures += 1
            self.retry_at = time.monotonic() + min(300, 5 * 2 ** min(self.failures - 1, 6))
            LOG.warning("PostgreSQL output failed: %s; snapshot may not be stored", type(exc).__name__)
        else:
            self.failures = 0
            self.retry_at = 0.0

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
