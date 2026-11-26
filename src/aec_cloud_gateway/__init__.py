"""Read-only AEC Cloud client; no network activity on import."""

from .client import AECClient
from .errors import (
    AECError, APIError, AuthenticationError, ProtocolError,
    RateLimitError, SessionExpiredError, TransportError,
)

__all__ = [
    "AECClient", "AECError", "APIError", "AuthenticationError",
    "ProtocolError", "RateLimitError", "SessionExpiredError", "TransportError",
]
