"""Read-only session observations. Never serialize cookie values or raw URLs."""
from collections.abc import Mapping
from datetime import datetime, timezone
import time


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def endpoint(path):
    if path.startswith("/energy/getHomeControlSn/"):
        return "get_home_control_sn"
    return {"/user/login": "login", "/plant/getPlantVos": "get_plants",
            "/energy/getHomeCountData": "summary",
            "/device/getDeviceBySn": "detail"}.get(path, "other")


class SessionHealth:
    def __init__(self):
        self.last_login_at = None
        self.login_monotonic = None
        self.http_401_count = 0
        self.api_login_required_count = 0
        self.cookie_rotations = 0
        self.cookie_missing_requests = 0
        self.cookie_header_unknown = 0
        self.last_read_cookie_sent = None
        self.last_rejection = None
        self.last_renewal = None
        self.lifetime_sum = 0.0
        self.lifetime_count = 0

    @staticmethod
    def cookie_values(jar):
        # Private transient comparison only; values are never stored in reports.
        return frozenset((c.domain, c.path, c.value) for c in jar
                         if c.name == "JSESSIONID" and c.value and not c.is_expired())

    def observe(self, path, before, jar, response):
        if endpoint(path) == "login":
            return
        after = self.cookie_values(jar)
        if before and after and before != after:
            self.cookie_rotations += 1
        headers = getattr(getattr(response, "request", None), "headers", None)
        if not isinstance(headers, Mapping):
            self.last_read_cookie_sent = None
            self.cookie_header_unknown += 1
            return
        # Inspect the prepared request, not a guessed cookie-jar match.
        raw = next((v for k, v in headers.items() if k.lower() == "cookie"), "")
        self.last_read_cookie_sent = any(
            part.partition("=")[0].strip() == "JSESSIONID" and bool(part.partition("=")[2].strip())
            for part in raw.split(";")
        )
        if not self.last_read_cookie_sent:
            self.cookie_missing_requests += 1

    def reject(self, path, reason, status):
        if reason == "http_401":
            self.http_401_count += 1
        else:
            self.api_login_required_count += 1
        age = self.age()
        self.last_rejection = {"at": utc_now(), "endpoint": endpoint(path),
                               "reason": reason, "http_status": status,
                               "session_age_seconds": age,
                               "cookie_sent": self.last_read_cookie_sent if endpoint(path) != "login" else None}

    def age(self):
        if self.login_monotonic is None:
            return None
        return round(max(0, time.monotonic() - self.login_monotonic), 2)

    def login(self):
        self.last_login_at = utc_now()
        self.login_monotonic = time.monotonic()

    def renewed(self):
        # Called only after successful re-login. The triggering observation
        # precedes login, and survives a subsequent failed retry.
        if self.last_rejection is not None:
            self.last_renewal = dict(self.last_rejection)
            age = self.last_renewal["session_age_seconds"]
            if age is not None:
                self.lifetime_count += 1
                self.lifetime_sum += age

    def snapshot(self, jar, authenticated):
        cookies = [c for c in jar if c.name == "JSESSIONID"]
        # No raw domain/path strings: they can contain internal identifiers.
        metadata = [{"domain_specified": c.domain_specified,
                     "path_is_root": c.path == "/",
                     "secure": c.secure, "session_cookie": c.discard,
                     "expires_at": datetime.fromtimestamp(c.expires, timezone.utc).isoformat()
                     if c.expires is not None and 0 <= c.expires <= 253402300799 else None,
                     "expired": c.is_expired()} for c in cookies]
        return {"authenticated": authenticated,
                "last_login_at": self.last_login_at,
                "session_age_seconds": self.age() if authenticated else None,
                "http_401_count": self.http_401_count,
                "api_login_required_count": self.api_login_required_count,
                "cookie_rotations": self.cookie_rotations,
                "cookie_missing_requests": self.cookie_missing_requests,
                "cookie_header_unknown": self.cookie_header_unknown,
                "last_read_cookie_sent": self.last_read_cookie_sent,
                "cookies": metadata,
                "last_rejection": self.last_rejection,
                "last_renewal": self.last_renewal,
                "session_lifetime_avg_seconds": round(self.lifetime_sum / self.lifetime_count, 2)
                if self.lifetime_count else None}
