"""Per-fix git worktree isolation: each approved fix gets its own checkout.

The main checkout stays pristine (and shareable between concurrent runs):
at approval time the orchestrator creates one worktree at the proposal's
recorded base commit, applies + tests + verifies inside it, then removes
the worktree while keeping the fix branch. Anything that looks off —
stale base, dirty tree, foreign paths — fails closed before touching code.

Cleanup ownership: remove_worktree only ever removes the exact path handed
to it, only under the runs' worktree base directory, and only while git
still lists it. It can never remove the main checkout or another run's tree.
"""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

GIT_TIMEOUT_SEC = 30
SERVER_START_TIMEOUT_SEC = 25
FIXTURE_SERVER = "server.py"


class WorktreeError(RuntimeError):
    """Why a worktree operation was refused. Safe for the audit trail."""


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT_SEC) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
    )


def repo_toplevel(workspace: Path) -> Path | None:
    """Repo root containing the workspace, or None outside any repo."""
    try:
        proc = _git(workspace, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def workspace_rel(workspace: Path, toplevel: Path) -> str:
    return workspace.resolve().relative_to(toplevel.resolve()).as_posix()


def worktrees_base(artifact_dir: str | Path) -> Path:
    return Path(artifact_dir).resolve() / "worktrees"


def create_worktree(repo: Path, path: Path, branch: str, base: str) -> None:
    """Checkout `base` into a new worktree on a new `branch`.

    A stale or unknown base makes `git worktree add` itself fail — the
    proposal is refused before anything is applied anywhere.
    """
    if not branch or not base:
        raise WorktreeError("branch and base commit are both required")
    proc = _git(repo, "worktree", "add", "-b", branch, str(path), base)
    if proc.returncode != 0:
        raise WorktreeError((proc.stdout + proc.stderr).strip()[-500:] or "worktree add failed")


def worktree_list(repo: Path) -> list[str]:
    proc = _git(repo, "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        return []
    return [
        line.split(None, 1)[1]
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def remove_worktree(repo: Path, path: Path, base_dir: Path) -> bool:
    """Remove exactly this run's worktree. Returns True when removed.

    Refuses (raises) paths outside the runs' worktree base directory, and
    quietly does nothing when git no longer lists the path (already gone).
    The fix branch is kept — only the throwaway checkout goes away.
    """
    target = path.resolve()
    if target != base_dir.resolve() and base_dir.resolve() not in target.parents:
        raise WorktreeError(f"refusing to remove path outside worktree base: {path}")
    listed = [Path(p).resolve() for p in worktree_list(repo)]
    if target not in listed:
        return False
    proc = _git(repo, "worktree", "remove", "--force", str(target))
    if proc.returncode != 0:
        raise WorktreeError((proc.stdout + proc.stderr).strip()[-500:] or "worktree remove failed")
    return True


def worktree_head(path: Path) -> str:
    proc = _git(path, "rev-parse", "HEAD")
    if proc.returncode != 0:
        raise WorktreeError("cannot read worktree HEAD")
    return proc.stdout.strip()


def worktree_clean(path: Path) -> bool:
    proc = _git(path, "status", "--porcelain")
    if proc.returncode != 0:
        return False
    return not any(line and not line.startswith("??") for line in proc.stdout.splitlines())


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def launch_fixture_server(workspace: Path, port: int, log_file: Path):
    """Start `<workspace>/server.py <port>` (the fixture's documented
    interface: stdlib only, serves the app from its own directory, so a
    worktree checkout is served with the worktree's code). Waits for a
    healthy root response; raises on timeout without leaving a process."""
    if not (workspace / FIXTURE_SERVER).is_file():
        raise WorktreeError(f"no {FIXTURE_SERVER} in workspace {workspace}")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, FIXTURE_SERVER, str(port)], cwd=workspace,
            stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.time() + SERVER_START_TIMEOUT_SEC
        url = f"http://127.0.0.1:{port}/"
        while time.time() < deadline:
            if proc.poll() is not None:
                raise WorktreeError(f"fixture server exited (port {port})")
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    if resp.status == 200:
                        return proc
            except OSError:
                pass
            time.sleep(0.5)
    except Exception:
        stop_server(proc)
        raise
    stop_server(proc)
    raise WorktreeError(f"fixture server unhealthy after {SERVER_START_TIMEOUT_SEC}s")


def stop_server(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def is_loopback(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


def rewrite_origin(url: str, from_url: str, to_base: str) -> str:
    """Swap one origin for another, keeping path/query. Non-absolute or
    foreign-origin URLs pass through untouched."""
    try:
        target, ref, base = urlparse(url), urlparse(from_url), urlparse(to_base)
    except ValueError:
        return url
    if not target.netloc or target.netloc != ref.netloc:
        return url
    return urlunparse((base.scheme or "http", base.netloc, target.path, "", target.query, ""))
