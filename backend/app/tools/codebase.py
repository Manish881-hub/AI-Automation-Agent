"""Sandboxed codebase access: the agent's hands for white-box debugging.

Every operation is confined to a workspace root (path traversal raises),
skips dependency/build/secret paths, caps output for LLM context, and
redacts secret-looking values. Writes happen only through apply_patch,
which the orchestrator gates behind explicit human approval.
"""

import fnmatch
import re
import subprocess
from pathlib import Path

# Never searched, read, or patched through the agent.
DENY_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "artifacts", ".next",
})
DENY_FILES = ("*.pem", "*.key", "*.p12", "*.pfx", ".env", ".env.*", "*secret*")
MAX_FILE_CHARS = 20000
MAX_RESULTS = 20
MAX_SEARCH_BYTES = 200_000
GIT_TIMEOUT_SEC = 30

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*([:=])\s*(\S+)"
)


def redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)


def _denied(path: Path) -> bool:
    return any(part in DENY_DIRS for part in path.parts) or any(
        fnmatch.fnmatch(path.name, pat) for pat in DENY_FILES
    )


class CodebaseTool:
    """Read-only search by default; apply_patch is approval-gated upstream."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _resolve(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path escapes workspace: {rel!r}")
        return candidate

    def list_files(self, pattern: str = "**/*.py", limit: int = 50) -> list[str]:
        found = [
            str(p.relative_to(self.root))
            for p in sorted(self.root.glob(pattern))
            if p.is_file() and not _denied(p.relative_to(self.root))
        ]
        return found[:limit]

    def read_file(self, rel: str, max_chars: int = MAX_FILE_CHARS) -> str:
        path = self._resolve(rel)
        if _denied(path.relative_to(self.root)):
            raise ValueError(f"refusing to read denied path: {rel!r}")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise ValueError(f"cannot read {rel!r}: {exc}") from exc
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… [truncated at {max_chars} chars]"
        return redact_secrets(text)

    def search(
        self, pattern: str, include: str = "*.py", limit: int = MAX_RESULTS
    ) -> list[dict]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"bad search pattern: {exc}") from exc
        hits: list[dict] = []
        for path in sorted(self.root.rglob("*")):
            if len(hits) >= limit:
                break
            rel = path.relative_to(self.root)
            if not path.is_file() or _denied(rel):
                continue
            if not fnmatch.fnmatch(path.name, include):
                continue
            try:
                if path.stat().st_size > MAX_SEARCH_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append({
                        "file": str(rel),
                        "line": lineno,
                        "text": redact_secrets(line.strip())[:300],
                    })
                    if len(hits) >= limit:
                        break
        return hits

    def _git(self, *args: str, timeout: int = GIT_TIMEOUT_SEC) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True,
            text=True, timeout=timeout,
        )
        out = (proc.stdout + proc.stderr).strip()
        return redact_secrets(out[-8000:])

    def git_status(self) -> str:
        return self._git("status", "--short")

    def git_log(self, n: int = 5) -> str:
        return self._git("log", f"-{max(1, min(n, 10))}", "--oneline")

    def git_diff(self) -> str:
        return self._git("diff", "--stat") + "\n" + self._git("diff", "--", ".")

    # --- gated writes: called only after human approval upstream ---

    def apply_check(self, diff_text: str) -> tuple[bool, str]:
        """Dry-run `git apply --check`. Mutates nothing."""
        proc = subprocess.run(
            ["git", "apply", "--check", "-"], input=diff_text,
            cwd=self.root, capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
        )
        out = redact_secrets((proc.stdout + proc.stderr).strip()[-2000:])
        return proc.returncode == 0, out

    def create_branch(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._/\-]+", name):
            raise ValueError(f"unsafe branch name: {name!r}")
        return self._git("checkout", "-b", name)

    def apply_patch(self, diff_text: str) -> str:
        proc = subprocess.run(
            ["git", "apply", "-"], input=diff_text,
            cwd=self.root, capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                redact_secrets((proc.stdout + proc.stderr).strip()[-2000:])
            )
        return self._git("status", "--short")
