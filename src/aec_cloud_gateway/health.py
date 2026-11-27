"""Process-local diagnostics; never expose arbitrary exception messages."""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError

from .errors import TransportError


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    # Only fixed messages from the client and validated numeric HTTP statuses.
    name = type(exc).__name__
    if isinstance(exc, TransportError):
        msg = str(exc)
        if msg in {"TLS verification failed", "Network retry budget exhausted",
                   "Timeout: network retry budget exhausted",
                   "Connection error: network retry budget exhausted",
                   "HTTP request failed", "Server retry delay exceeds configured maximum"}:
            return f"{name}: {msg}"
        if re.fullmatch(r"(?:HTTP [1-5][0-9]{2}: retry budget exhausted|Unexpected HTTP status [1-5][0-9]{2})", msg):
            return f"{name}: {msg}"
    return name


@dataclass
class PollStats:
    successful_polls: int = 0
    failed_polls: int = 0
    offline_polls: int = 0
    consecutive_failures: int = 0
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    last_duration_ms: float | None = None
    duration_sum_ms: float = 0.0

    def record(self, duration, error=None, offline=False):
        self.last_duration_ms = round(duration * 1000, 2)
        self.duration_sum_ms += duration * 1000
        if error is not None or offline:
            self.failed_polls += 1
            self.offline_polls += int(offline)
            self.consecutive_failures += 1
            self.last_error = "InverterOfflineOrUnknown" if offline else safe_error(error)
            self.last_error_at = utc_now()
        else:
            self.successful_polls += 1
            self.consecutive_failures = 0
            self.last_success_at = utc_now()

    def snapshot(self, schedule, now, blocked=False):
        count = self.successful_polls + self.failed_polls
        return {"successful_polls": self.successful_polls, "failed_polls": self.failed_polls,
                "offline_polls": self.offline_polls, "consecutive_failures": self.consecutive_failures,
                "last_success_at": self.last_success_at, "last_error_at": self.last_error_at,
                "last_error": self.last_error, "last_duration_ms": self.last_duration_ms,
                "avg_duration_ms": round(self.duration_sum_ms / count, 2) if count else None,
                "blocked": blocked,
                "next_poll_in_seconds": None if blocked else round(max(0, schedule.due - now), 1)}


class Health:
    def __init__(self):
        self.started = time.monotonic()
        self.started_at = utc_now()
        self.stats = {key: PollStats() for key in ("discovery", "summary", "detail")}
        try:
            self.version = version("aec-cloud-gateway")
        except PackageNotFoundError:
            self.version = "unknown"

    def record(self, source, started, error=None, offline=False):
        self.stats[source].record(time.monotonic() - started, error, offline)

    def snapshot(self, client, schedules, discovery, selected, summary_ok, halted,
                 mqtt_output, database, now):
        return {"schema": 1, "version": self.version, "reported_at": utc_now(),
                "started_at": self.started_at, "uptime_seconds": int(max(0, now - self.started)),
                "polling_halted": halted, "device_selected": selected,
                "api": {"request_attempts": client.request_attempts,
                        "request_retries": client.request_retries,
                        "successful_logins": client.successful_logins,
                        "session_renewals": client.session_renewals,
                        "session": client.session_health.snapshot(client._session.cookies, client._authenticated)},
                "discovery": self.stats["discovery"].snapshot(discovery, now, selected or halted),
                "summary": self.stats["summary"].snapshot(schedules["summary"], now, not selected or halted),
                "detail": self.stats["detail"].snapshot(schedules["detail"], now, not selected or halted or not summary_ok),
                "mqtt": mqtt_output.health() if mqtt_output else {"enabled": False},
                "postgresql": database.health(now) if database else {"enabled": False}}
