"""Per-run session audit trail: what the agent knew, did, and decided.

Every orchestrated run gets a Session with timestamped records. Persisted
to artifacts/<run_id>/session.json so any failure is fully reproducible.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .artifacts import save_text


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    run_id: str
    created_at: str = field(default_factory=_utcnow)
    events: list[dict] = field(default_factory=list)

    def record(self, agent: str, event: str, detail: str = "") -> None:
        self.events.append(
            {
                "ts": _utcnow(),
                "agent": agent,
                "event": event,
                "detail": detail,
            }
        )


class SessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def create(self, run_id: str) -> Session:
        session = Session(run_id=run_id)
        self.sessions[run_id] = session
        return session

    def get(self, run_id: str) -> Session | None:
        return self.sessions.get(run_id)

    def persist(self, run_id: str) -> str:
        session = self.sessions.get(run_id)
        if session is None:
            session = self.create(run_id)
        payload = {
            "run_id": session.run_id,
            "created_at": session.created_at,
            "events": session.events,
        }
        return save_text(run_id, "session.json", json.dumps(payload, indent=2))


session_store = SessionStore()
