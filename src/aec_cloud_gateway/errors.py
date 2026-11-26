class AECError(Exception):
    """Base exception; messages deliberately exclude credentials and response bodies."""


class AuthenticationError(AECError):
    """Credentials rejected or session could not be established."""


class SessionExpiredError(AuthenticationError):
    """The API still requires login after a single session renewal."""


class TransportError(AECError):
    """Connection, timeout, or HTTP failure."""


class RateLimitError(TransportError):
    """Retry budget exhausted or server requests a longer delay."""


class ProtocolError(AECError):
    """Unexpected response format or missing login success marker."""


class APIError(AECError):
    """AEC application-level failure."""
