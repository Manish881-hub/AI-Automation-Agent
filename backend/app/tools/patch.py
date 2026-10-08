"""Deterministic patch engine: turns LLM edit intents into git diffs.

The model states WHAT to change (file + old/new text); this module
decides WHETHER the change is safe and builds the diff itself with
difflib, then proves it with `git apply --check`. Every rejection is
explicit: missing file, denied path, absent or ambiguous anchor text,
empty or no-op edits. The model never writes patch syntax.
"""

import difflib


class PatchEngineError(ValueError):
    """Why a proposed edit was rejected. Message goes to the audit trail."""


def build_diff(tool, edits) -> str:
    """Validate edits and return a git-apply-clean unified diff.

    Raises PatchEngineError with a specific reason on any failure.
    Reads files, writes nothing.
    """
    if not edits:
        raise PatchEngineError("empty edit list")
    # Group edits per file, applied in order on evolving content.
    by_file: dict[str, list] = {}
    for edit in edits:
        rel = (edit.file or "").strip()
        if not rel:
            raise PatchEngineError("edit with empty file path")
        by_file.setdefault(rel, []).append(edit)
    parts: list[str] = []
    for rel, file_edits in by_file.items():
        try:
            original = tool.read_file(rel, max_chars=200_000).splitlines()
        except ValueError as exc:
            raise PatchEngineError(f"{rel}: {exc}") from exc
        if _denied_rel(tool, rel):
            raise PatchEngineError(f"{rel}: path outside sandbox")
        content = list(original)
        for edit in file_edits:
            old = edit.old_text
            new = edit.new_text
            if not old.strip():
                raise PatchEngineError(f"{rel}: empty old_text")
            if old == new:
                raise PatchEngineError(f"{rel}: old_text and new_text identical")
            old_lines = old.splitlines()
            hits = [
                i for i in range(len(content) - len(old_lines) + 1)
                if content[i:i + len(old_lines)] == old_lines
            ]
            if not hits:
                raise PatchEngineError(f"{rel}: old_text not present (source changed?)")
            if len(hits) > 1:
                raise PatchEngineError(
                    f"{rel}: old_text occurs {len(hits)} times, ambiguous"
                )
            idx = hits[0]
            content[idx:idx + len(old_lines)] = new.splitlines()
        header = f"diff --git a/{rel} b/{rel}\n"
        parts.append(
            header
            + "\n".join(difflib.unified_diff(
                original, content,
                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="",
            )) + "\n"
        )
    diff = "".join(parts)
    ok, out = tool.apply_check(diff)
    if not ok:
        raise PatchEngineError(f"generated diff failed apply check: {out[:200]}")
    return diff


def _denied_rel(tool, rel: str) -> bool:
    from .codebase import _denied
    from pathlib import Path

    return _denied(Path(rel))
