"""Local auth service for the closed-loop demo.

Premise (documented, deterministic): the upstream token service is
degraded and returns an empty payload. Valid credentials must still
yield a limited session so users can reach the dashboard.

BUG (intentional): direct ``provider_response[\"token\"]`` access raises
KeyError on the degraded payload instead of falling back.
"""

from typing import Any

VALID_USERS = {"test@example.com": "Test1234!"}


def fetch_provider_token(username: str) -> dict[str, Any]:
    """Simulated degraded token service: no token issued right now."""
    return {}


def authenticate(username: str, password: str) -> dict[str, Any]:
    """Authenticate a user. Returns a session dict on success."""
    if VALID_USERS.get(username) != password:
        return {"ok": False, "error": "invalid credentials"}
    provider_response = fetch_provider_token(username)
    token = provider_response["token"]
    return {"ok": True, "token": token, "limited": False}
