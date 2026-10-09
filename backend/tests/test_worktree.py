"""Per-fix git worktree isolation: concurrent runs, correct test target,
worktree-served verification, stale-base refusal, safe cleanup.

All git/server work happens in throwaway repos under tmp_path — the real
checkout is never touched. Fixture layout mirrors the demo app: a
workspace dir with code, a stdlib-only server.py serving that dir's code,
and a pytest suite expressing the fixed behavior.
"""

import subprocess
import urllib.request
from pathlib import Path

import pytest

from app.schemas import PatchEdit, TestStep
from app.services.orchestrator import Orchestrator, RunState
from app.services.session import session_store
from app.tools.codebase import CodebaseTool
from app.tools.patch import build_diff
from app.tools.testrunner import TestRunnerTool
from app.tools.worktree import (
    WorktreeError, create_worktree, find_free_port, is_loopback,
    launch_fixture_server, remove_worktree, repo_toplevel, rewrite_origin,
    stop_server, workspace_rel, worktree_clean, worktree_head,
    worktree_list, worktrees_base,
)

SERVER_STUB = """\
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
BASE = Path(__file__).resolve().parent
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        marker = (BASE / "data.txt").read_text().strip()
        body = ("ok:" + marker).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""

APP_PY = 'MARKER = "buggy"\n'
TEST_PY = """\
from pathlib import Path
def test_fixed():
    assert (Path(__file__).parent.parent / "data.txt").read_text().strip() == "fixed"
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, timeout=30)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    ws = root / "demo-app"
    (ws / "tests").mkdir(parents=True)
    (ws / "app.py").write_text(APP_PY)
    (ws / "data.txt").write_text("buggy\n")
    (ws / "tests" / "test_app.py").write_text(TEST_PY)
    (ws / "server.py").write_text(SERVER_STUB)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.email=t@t.t", "-c", "user.name=t", "commit", "-qm", "init")
    _git(root, "-c", "user.email=t@t.t", "-c", "user.name=t",
         "config", "user.email", "t@t.t")
    _git(root, "-c", "user.email=t@t.t", "-c", "user.name=t",
         "config", "user.name", "t")
    return root


def _ws(repo: Path) -> Path:
    return repo / "demo-app"


def _base(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.strip()


def _http_get(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as resp:
        return resp.read().decode()


def test_distinct_worktrees_make_independent_changes(repo):
    base = _base(repo)
    wt_base = repo / "wt"
    path_a, path_b = wt_base / "run-a", wt_base / "run-b"
    create_worktree(repo, path_a, "fix/a", base)
    create_worktree(repo, path_b, "fix/b", base)
    assert path_a != path_b

    tool_a = CodebaseTool(str(path_a / "demo-app"))
    tool_b = CodebaseTool(str(path_b / "demo-app"))
    diff_a = build_diff(tool_a, [PatchEdit(
        file="data.txt", old_text="buggy", new_text="fixed")])
    diff_b = build_diff(tool_b, [PatchEdit(
        file="data.txt", old_text="buggy", new_text="other")])
    assert tool_a.apply_patch(diff_a)
    assert tool_b.apply_patch(diff_b)
    assert (path_a / "demo-app" / "data.txt").read_text() == "fixed\n"
    assert (path_b / "demo-app" / "data.txt").read_text() == "other\n"
    # The main checkout is untouched by either worktree.
    assert (_ws(repo) / "data.txt").read_text() == "buggy\n"


def test_pytest_runs_against_worktree_code(repo):
    base = _base(repo)
    wt = repo / "wt" / "run-a"
    create_worktree(repo, wt, "fix/a", base)
    main_runner = TestRunnerTool(str(_ws(repo)), timeout=60)
    assert main_runner.run_pytest([])["passed"] is False  # buggy baseline
    tool = CodebaseTool(str(wt / "demo-app"))
    tool.apply_patch(build_diff(tool, [PatchEdit(
        file="data.txt", old_text="buggy", new_text="fixed")]))
    wt_runner = TestRunnerTool(str(wt / "demo-app"), timeout=60)
    assert wt_runner.run_pytest([])["passed"] is True


def test_worktree_server_serves_fixed_code(repo):
    base = _base(repo)
    wt = repo / "wt" / "run-a"
    create_worktree(repo, wt, "fix/a", base)
    (wt / "demo-app" / "data.txt").write_text("fixed")
    log = repo / "wt.log"
    port_fixed, port_buggy = find_free_port(), find_free_port()
    assert port_fixed != port_buggy
    proc_fixed = launch_fixture_server(wt / "demo-app", port_fixed, log)
    proc_buggy = launch_fixture_server(_ws(repo), port_buggy, log)
    try:
        assert _http_get(port_fixed) == "ok:fixed"
        assert _http_get(port_buggy) == "ok:buggy"
    finally:
        stop_server(proc_fixed)
        stop_server(proc_buggy)


def test_stale_base_commit_refused(repo, tmp_path):
    wt = tmp_path / "wt" / "run-a"
    with pytest.raises(WorktreeError):
        create_worktree(repo, wt, "fix/stale", "0" * 40)
    assert not wt.exists()


def test_reject_creates_no_worktree_and_applies_nothing(repo, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "workspace_root", str(_ws(repo)))
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.fix_status = "awaiting_approval"
    run.fix_diff = "diff --git a/data.txt b/data.txt\n"
    session = session_store.create(run.run_id)
    try:
        out = orch.reject_fix(run.run_id)
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert out.fix_status == "rejected"
    assert out.fix_worktree == ""
    assert (_ws(repo) / "data.txt").read_text() == "buggy\n"
    # Only the main checkout itself is listed — rejection added no worktree.
    assert worktree_list(repo) == [str(repo)]


def test_cleanup_removes_own_tree_keeps_branch_and_artifacts(repo, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "workspace_root", str(_ws(repo)))
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    base = _base(repo)
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.fix_status = "verified"
    run.fix_branch = "fix/t1"
    wt_path = worktrees_base(settings.artifact_dir) / run.run_id
    create_worktree(repo, wt_path, "fix/t1", base)
    run.fix_worktree = str(wt_path)
    assert worktree_clean(wt_path) and worktree_head(wt_path) == base
    out = orch._finish_fix(run)
    assert not wt_path.exists()
    assert wt_path.as_posix() not in worktree_list(repo)
    # Branch (the fix) and main checkout survive cleanup.
    branches = subprocess.run(["git", "branch", "--list", "fix/t1"], cwd=repo,
                              capture_output=True, text=True, timeout=30).stdout
    assert "fix/t1" in branches
    assert (_ws(repo) / "data.txt").read_text() == "buggy\n"
    assert any("worktree_cleaned" in e for e in out.events)
    session_store.sessions.pop(run.run_id, None)


def test_cleanup_refuses_foreign_paths(repo, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "workspace_root", str(_ws(repo)))
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path / "artifacts"))
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    session = session_store.create(run.run_id)
    try:
        run.fix_worktree = str(repo)  # the main checkout itself
        orch._cleanup_worktree(run, session)
        assert repo.exists()  # untouched
        assert any("worktree_cleanup_failed" in e for e in run.events)
    finally:
        session_store.sessions.pop(run.run_id, None)


def test_remove_worktree_refuses_outside_base(repo, tmp_path):
    with pytest.raises(WorktreeError):
        remove_worktree(repo, tmp_path / "elsewhere", repo / "wt")


def test_rewrite_step_origins():
    run_url = "http://localhost:8921/"
    base = "http://127.0.0.1:51234"
    nav = TestStep(id=1, action="navigate", target="http://localhost:8921/")
    assert Orchestrator._rewrite_step(nav, run_url, base).target == base + "/"
    foreign = TestStep(id=1, action="navigate", target="https://wellfound.com/jobs")
    assert Orchestrator._rewrite_step(foreign, run_url, base).target == foreign.target
    url_assert = TestStep(id=6, action="assert", assertion_type="url",
                          target="URL Bar", value="http://localhost:8921/dashboard")
    assert Orchestrator._rewrite_step(url_assert, run_url, base).value == base + "/dashboard"
    text_assert = TestStep(id=2, action="assert", assertion_type="text",
                           target="Welcome", value="Welcome")
    out = Orchestrator._rewrite_step(text_assert, run_url, base)
    assert out.target == "Welcome" and out.value == "Welcome"


def test_loopback_and_repo_helpers(repo):
    assert is_loopback("http://localhost:8921/")
    assert is_loopback("http://127.0.0.1:9999/x")
    assert not is_loopback("https://wellfound.com/jobs")
    assert repo_toplevel(_ws(repo)) == repo
    assert workspace_rel(_ws(repo), repo) == "demo-app"
    assert rewrite_origin("http://a:1/x?y=2", "http://a:1/", "http://b:3") == "http://b:3/x?y=2"
    assert rewrite_origin("https://other/x", "http://a:1/", "http://b:3") == "https://other/x"
