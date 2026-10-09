"""Durable run history: an append-only JSONL index of terminal transitions.

Runs live in memory, so a backend restart used to erase them (a finished
golden run became a 404). Every terminal transition now appends one small
summary record — run completed/failed, fix reached a terminal state —
under backend/artifacts/runs.jsonl. The per-run artifact directories keep
the full detail; this file is the restart-proof index over them.

Single-node safety: one process-wide lock plus a single O_APPEND write
per record (small enough to land atomically), flushed and fsynced.
Readers skip blank lines, torn tails, and malformed records instead of
dropping the whole history. Recording never raises: a history failure
must not fail the run it describes.
"""

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings

_lock = threading.Lock()


def history_path() -> Path:
    return Path(settings.artifact_dir) / "runs.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_terminal(run, transition: str) -> bool:
    """Append one terminal-transition record. Returns True when recorded.

    Repeats of an already-recorded (run, transition) pair are skipped, so
    retries of the recording path cannot fork history; distinct
    transitions for one run (run_failed, then later fix_terminal) are each
    kept and identifiable by run_id + transition + timestamp.
    """
    recorded = getattr(run, "recorded_transitions", None)
    if recorded is not None and transition in recorded:
        return False
    record = {
        "run_id": run.run_id,
        "transition": transition,
        "ts": _utcnow(),
        "status": run.status,
        "url": run.url,
        "objective": run.objective,
        "objective_status": run.objective_status,
        "objective_reason": run.objective_reason,
        "error_stage": run.error_stage,
        "error_kind": run.error_kind,
        "error_message": run.error_message,
        "fix_status": run.fix_status,
        "fix_branch": run.fix_branch,
        "llm_calls": run.llm_calls,
    }
    line = json.dumps(record, default=str) + "\n"
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
    except OSError as exc:
        print(f"[history] append failed for run {run.run_id}: {exc}", file=sys.stderr)
        return False
    if recorded is not None:
        recorded.add(transition)
    return True


def read_history(limit: int = 50) -> list[dict]:
    """Terminal-transition summaries, newest first.

    Tolerates a missing file (no history yet), blank lines, a torn final
    line from a crashed writer, and malformed records — each bad line is
    skipped, the rest survive.
    """
    try:
        text = history_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("run_id"):
            records.append(item)
    limit = max(1, min(limit, 200))
    return records[-limit:][::-1]
