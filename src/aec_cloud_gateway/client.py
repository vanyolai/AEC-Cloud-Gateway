"""Synchronous, single-threaded client for the observed AEC mobile API."""

from __future__ import annotations

import hashlib
import math
import os
import random
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from .session_health import SessionHealth

from .errors import (
    APIError, AuthenticationError, ProtocolError, RateLimitError,
    SessionExpiredError, TransportError,
)

BASE_URL = "https://monitor.ai-ec.cloud:8443"
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_LOGIN_REQUIRED = ("please login", "please log in", "session expired", "not logged in")


class _LoginRequired(Exception):
    pass


def _identifier(value: str | int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected a string or integer identifier")
    value = str(value)
    if not value or not value.isascii() or not all(c.isalnum() or c in "-_" for c in value):
        raise ValueError("Invalid identifier")
    return value


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class AECClient:
    """Own a cookie session and expose only observed telemetry operations.

    Instances are not thread-safe. Use one client per worker. Responses are
    returned as JSON dictionaries without guessing units, signs or envelopes.
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        if not email or not password:
            raise ValueError("AEC email and password are required")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("base_url must be an HTTPS origin without credentials")
        if (not isinstance(max_retries, int) or isinstance(max_retries, bool)
                or not 0 <= max_retries <= 5):
            raise ValueError("max_retries must be an integer between 0 and 5")
        if not all(math.isfinite(n) and n > 0 for n in (timeout, backoff_base, max_backoff)):
            raise ValueError("Timeout and backoff settings must be finite and positive")
        if backoff_base > max_backoff:
            raise ValueError("backoff_base must not exceed max_backoff")
        self._email = email
        # MD5 is mandated by the observed API, not a password storage scheme.
        self._password_md5 = hashlib.md5(password.encode("utf-8"), usedforsecurity=False).hexdigest()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_backoff = max_backoff
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._authenticated = False
        self._closed = False
        self.request_attempts = 0
        self.request_retries = 0
        self.successful_logins = 0
        self.session_renewals = 0
        self.session_health = SessionHealth()

    @classmethod
    def from_env(cls, **kwargs: Any) -> AECClient:
        return cls(os.environ.get("AEC_EMAIL", ""), os.environ.get("AEC_PASSWORD", ""), **kwargs)

    def __enter__(self) -> AECClient:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._authenticated = False
        self._closed = True
        self._session.cookies.clear()
        self._session.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AECClient is closed")

    def _wait(self, attempt: int, retry_after: str | None = None) -> None:
        delay = _retry_after(retry_after)
        if delay is not None and delay > self._max_backoff:
            raise RateLimitError("Server retry delay exceeds configured maximum")
        ceiling = min(self._max_backoff, self._backoff_base * 2 ** attempt)
        time.sleep(max(random.uniform(ceiling / 2, ceiling), delay or 0.0))

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self._ensure_open()
        for attempt in range(self._max_retries + 1):
            self.request_attempts += 1
            if attempt:
                self.request_retries += 1
            before = self.session_health.cookie_values(self._session.cookies)
            try:
                response = self._session.request(
                    method, self._base_url + path, timeout=self._timeout,
                    allow_redirects=False, **kwargs,
                )
            except requests.exceptions.SSLError:
                raise TransportError("TLS verification failed") from None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                if attempt == self._max_retries:
                    reason = "Timeout" if isinstance(exc, requests.exceptions.Timeout) else "Connection error"
                    raise TransportError(f"{reason}: network retry budget exhausted") from None
                self._wait(attempt)
                continue
            except requests.exceptions.RequestException:
                raise TransportError("HTTP request failed") from None
            self.session_health.observe(path, before, self._session.cookies, response)
            status = response.status_code
            if status in _RETRY_STATUS:
                if attempt == self._max_retries:
                    error = RateLimitError if status == 429 else TransportError
                    raise error(f"HTTP {status}: retry budget exhausted")
                self._wait(attempt, response.headers.get("Retry-After"))
                continue
            if status == 401:
                self.session_health.reject(path, "http_401", status)
                raise _LoginRequired()
            if not 200 <= status < 300:
                raise TransportError(f"Unexpected HTTP status {status}")
            try:
                payload = response.json()
            except ValueError:
                raise ProtocolError("Expected a JSON response") from None
            if not isinstance(payload, dict):
                raise ProtocolError("Expected a JSON object")
            result = payload.get("result")
            if "result" in payload and (type(result) is not int or result != 0):
                msg = payload.get("msg")
                if isinstance(msg, str) and any(s in msg.casefold() for s in _LOGIN_REQUIRED):
                    self.session_health.reject(path, "api_login_required", status)
                    raise _LoginRequired()
                raise APIError("AEC API returned a non-success result")
            # Also recognize an explicit login-required message without a result.
            if "result" not in payload:
                msg = payload.get("msg")
                if isinstance(msg, str) and any(s in msg.casefold() for s in _LOGIN_REQUIRED):
                    self.session_health.reject(path, "api_login_required", status)
                    raise _LoginRequired()
            return payload
        raise AssertionError("Unreachable retry loop")

    def login(self) -> None:
        """Establish a fresh JSESSIONID session without returning credentials."""
        self._ensure_open()
        self._authenticated = False
        self._session.cookies.clear()
        try:
            payload = self._request("POST", "/user/login", json={
                "email": self._email,
                "password": self._password_md5,
                "phoneOs": 1,
                "phoneModel": "1.1",
                "appVersion": "V1.1",
            })
        except (APIError, _LoginRequired):
            raise AuthenticationError("AEC login rejected") from None
        if type(payload.get("result")) is not int or payload["result"] != 0:
            raise ProtocolError("Login response lacks the observed success marker")
        if not any(c.name == "JSESSIONID" and c.value and not c.is_expired()
                   for c in self._session.cookies):
            raise AuthenticationError("Login did not establish a JSESSIONID cookie")
        self._authenticated = True
        self.successful_logins += 1
        self.session_health.login()

    def _read(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self._ensure_open()
        if not self._authenticated:
            self.login()
        try:
            return self._request(method, path, **kwargs)
        except _LoginRequired:
            self._authenticated = False
            self.login()
            self.session_renewals += 1
            self.session_health.renewed()
        try:
            return self._request(method, path, **kwargs)
        except _LoginRequired:
            self._authenticated = False
            self._session.cookies.clear()
            raise SessionExpiredError("Session rejected after one renewal") from None

    def get_plants(self) -> dict[str, Any]:
        return self._read("GET", "/plant/getPlantVos")

    def get_home_control_sn(self, plant_id: str | int) -> dict[str, Any]:
        return self._read("GET", "/energy/getHomeControlSn/" + quote(_identifier(plant_id), safe=""))

    def get_home_count_data(self, plant_id: str | int, device_sn: str) -> dict[str, Any]:
        return self._read("POST", "/energy/getHomeCountData", params={
            "plantId": _identifier(plant_id), "deviceSn": _identifier(device_sn),
        })

    def get_device_by_sn(self, inverter_sn: str, day: date | str) -> dict[str, Any]:
        """Query inverter serial (not logger serial), deviceType=55, ISO date."""
        if isinstance(day, str):
            try:
                normalized = date.fromisoformat(day).isoformat()
            except ValueError:
                raise ValueError("day must use YYYY-MM-DD") from None
            if normalized != day:
                raise ValueError("day must use YYYY-MM-DD")
        elif type(day) is date:
            normalized = day.isoformat()
        else:
            raise ValueError("day must be a date or YYYY-MM-DD string")
        return self._read("POST", "/device/getDeviceBySn", params={
            "deviceType": 55, "time": normalized, "sn": _identifier(inverter_sn),
        })
