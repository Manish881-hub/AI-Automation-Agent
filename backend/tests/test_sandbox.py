"""Execution sandbox: confinement flags, secret-free env, fail-closed selection.

Docker command construction and lifecycle use fakes — no daemon needed.
Only the image build itself (scripts/setup-sandbox.sh) touches Docker.
"""

import subprocess
import threading
from types import SimpleNamespace

import pytest

from app.config import settings
from app.tools import sandbox as sandbox_module
from app.tools.codebase import CodebaseTool
from app.tools.environ import minimal_env, pytest_env
from app.tools.sandbox import (
    DockerSandboxRunner, LocalRunner, SandboxRunner, SandboxUnavailable,
    docker_available, select_runner,
)


@pytest.fixture()
def trap_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-trap-value-1234567890abcdef")
    monkeypatch.setenv("APPROVAL_TOKEN", "trap-token-value-1234567890abcdef")


class FakePopen:
    """Deterministic docker CLI stand-in. _script drives communicate()."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.script = list(kwargs.pop("_script", [("out", "ok", 0)]))
        self.terminated = False
        self.killed = False
        self.returncode = None

    def communicate(self, timeout=None):
        if not self.script:
            self.returncode = 0
            return ("", "")
        action = self.script.pop(0)
        if action[0] == "timeout":
            raise subprocess.TimeoutExpired("docker", timeout)
        _, out, rc = action
        self.returncode = rc
        return (out, "")

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


@pytest.fixture(autouse=True)
def _no_image_check(monkeypatch):
    # Unit tests fake the container boundary; image identity has its own
    # tests below. Without this, tests would need a real Docker daemon.
    monkeypatch.setattr(sandbox_module, "verify_image", lambda image: None)


def _runner(tmp_path, script, **overrides):
    made = []

    def factory(argv, **kwargs):
        proc = FakePopen(argv, **{**kwargs, "_script": list(script)})
        made.append(proc)
        return proc

    params = dict(image="agent-sandbox:1.0", timeout=60, popen_factory=factory)
    params.update(overrides)
    return DockerSandboxRunner(tmp_path, **params), made


def test_docker_argv_confinement(tmp_path):
    runner, made = _runner(tmp_path, [("out", "2 passed", 0)])
    result = runner.run_pytest([])
    assert result["passed"] is True
    argv = made[0].argv
    joined = " ".join(argv)
    for flag in ("--network", "none", "--user", "nobody", "--read-only",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--memory", "--memory-swap", "--cpus", "--pids-limit"):
        assert flag in argv, flag
    assert f"{tmp_path}:/work:ro" in argv  # workspace read-only by default
    assert not any("docker.sock" in a or "--privileged" in a for a in argv)
    assert "latest" not in joined  # pinned image only
    assert settings.sandbox_image in argv
    assert "OPENAI_API_KEY" not in joined and "APPROVAL_TOKEN" not in joined
    env_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert env_args and all("=" in e for e in env_args)
    assert not any("KEY" in e or "TOKEN" in e for e in env_args)


def test_unique_container_names(tmp_path):
    runner, made = _runner(tmp_path, [("out", "ok", 0)])
    runner.run_pytest([])
    runner2, made2 = _runner(tmp_path, [("out", "ok", 0)])
    runner2.run_pytest([])
    names = [a[a.index("--name") + 1] for proc in (made + made2) for a in [proc.argv]]
    assert names[0] != names[1]


def test_cancel_terminates_and_reaps_container(tmp_path, monkeypatch):
    rms = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: rms.append(a[0]) or SimpleNamespace(returncode=0))
    runner, made = _runner(tmp_path, [("timeout",)] * 50)
    event = threading.Event()
    event.set()  # operator already asked
    result = runner.run_pytest([], event)
    assert result["cancelled"] is True
    assert result["passed"] is False
    # The container (not just the CLI) is force-removed: killing the CLI
    # alone would orphan it. The CLI proc itself is reaped afterwards.
    assert any(cmd[:3] == ["docker", "rm", "-f"] for cmd in rms)
    rm_cmd = next(cmd for cmd in rms if cmd[:3] == ["docker", "rm", "-f"])
    assert rm_cmd[3] in made[0].argv  # the owned container, not another one
    assert made[0].poll() is not None  # reaped, no zombie CLI


def test_timeout_kills_container_without_cancel(tmp_path, monkeypatch):
    rms = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: rms.append(a[0]) or SimpleNamespace(returncode=0))
    runner, made = _runner(tmp_path, [("timeout",)] * 50, timeout=0)
    result = runner.run_pytest([])
    assert result["cancelled"] is False
    assert "timed out" in result["output"]
    assert any(cmd[:3] == ["docker", "rm", "-f"] for cmd in rms)


def test_missing_docker_fails_closed_unless_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_module, "docker_available", lambda: False)
    monkeypatch.setattr(settings, "allow_insecure_local_exec", False)
    with pytest.raises(SandboxUnavailable):
        select_runner(tmp_path, 60)
    warnings: list[str] = []
    monkeypatch.setattr(settings, "allow_insecure_local_exec", True)
    runner = select_runner(tmp_path, 60, on_insecure=warnings.append)
    assert isinstance(runner, LocalRunner)
    assert warnings and "INSECURE" in warnings[0]


def test_docker_preferred_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_module, "docker_available", lambda: True)
    monkeypatch.setattr(settings, "allow_insecure_local_exec", False)
    runner = select_runner(tmp_path, 60)
    assert isinstance(runner, DockerSandboxRunner)


def test_docker_probe_without_daemon(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert docker_available() is False


def test_image_identity_enforced(monkeypatch):
    # The autouse fixture neuters verify_image for container-fake tests;
    # drop it here: this test drives the real function with a fake CLI.
    monkeypatch.undo()
    from app.tools.sandbox import verify_image

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "missing:1.0" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such image")
        return SimpleNamespace(
            returncode=0,
            stdout="sha256:abc [agent-sandbox@sha256:3386a96c]",
            stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox_module, "_pinned_digest",
                        lambda: "sha256:3386a96c")
    with pytest.raises(SandboxUnavailable) as missing:
        verify_image("missing:1.0")
    assert "setup-sandbox" in str(missing.value)
    verify_image("agent-sandbox:1.0")  # digest matches
    monkeypatch.setattr(sandbox_module, "_pinned_digest",
                        lambda: "sha256:something-else")
    with pytest.raises(SandboxUnavailable) as swapped:
        verify_image("agent-sandbox:1.0")
    assert "digest" in str(swapped.value)
    assert any(a[0] == "docker" and "inspect" in a for a in calls)
    # Controlled environment for the Docker CLI itself.
    assert calls[0][0] == "docker"


def test_host_env_never_reaches_subprocesses(tmp_path, trap_env, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: seen.update(kwargs=k) or SimpleNamespace(
            stdout="", stderr="", returncode=0))
    CodebaseTool(str(tmp_path)).git_status()
    env = seen["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in env and "APPROVAL_TOKEN" not in env
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE"}


def test_minimal_env_contents():
    env = minimal_env()
    assert set(env) == {"PATH", "HOME", "LANG"}
    assert "OPENAI_API_KEY" not in pytest_env()
    assert pytest_env()["PYTHONDONTWRITEBYTECODE"] == "1"


def test_run_reads_require_operator(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "approval_token", "op-token")
    anonymous = TestClient(app)
    assert anonymous.post("/api/runs", json={
        "url": "http://localhost:8921/",
        "objective": "verify that a user can sign in and reach the dashboard"}).status_code == 401
    assert anonymous.get("/api/runs").status_code == 401
    assert anonymous.get("/api/runs/abc").status_code == 401
    assert anonymous.get("/api/runs/abc/events").status_code == 401
    assert anonymous.get("/health").status_code == 200  # diagnostics stay public


def test_authenticated_run_creation_wiring(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    created: list[str] = []

    class FakeOrchestrator:
        def create_run(self, url, objective):
            created.append(url)
            return SimpleNamespace(run_id="r-1", status="queued",
                                   objective=objective, url=url)

        async def execute(self, run_id):
            return None

    monkeypatch.setattr(settings, "approval_token", "op-token")
    monkeypatch.setattr("app.api.runs.orchestrator", FakeOrchestrator())
    resp = TestClient(app).post(
        "/api/runs",
        json={"url": "http://localhost:8921/",
              "objective": "verify that a user can sign in and reach the dashboard"},
        headers={"X-Approval-Token": "op-token"})
    assert resp.status_code == 202
    assert created == ["http://localhost:8921/"]
