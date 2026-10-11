"""Isolated fixture-server execution: per-run internal networks, clean env,
owned lifecycle, scrubbed browser launch.

Routine lifecycle uses fakes (no daemon needed); one live smoke test runs
real Docker where available and proves the properties instead of assuming
them: host reaches the app, the app reaches nothing. Real-LLM replay stays
with the golden acceptance run.
"""

import socket
import subprocess
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.tools import sandbox as sandbox_module
from app.tools.browser import BrowserTool
from app.tools.environ import browser_env
from app.tools.sandbox import (
    DockerServerRunner, SandboxUnavailable, ServerError,
)

MARKER_SERVER = """\
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
BASE = Path(__file__).resolve().parent
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("marker:" + (BASE / "data.txt").read_text().strip()).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


class _FakeStdout:
    @staticmethod
    def read():
        return ""


class FakePopen:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.returncode = 0
        self._stdout = _FakeStdout()

    @property
    def stdout(self):
        return self._stdout

    def wait(self, timeout=None):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    @property
    def stdout(self):
        return SimpleNamespace(read=lambda: "")


class FakeDocker:
    """Records docker CLI calls; answers network create/inspect/logs/rm."""

    def __init__(self, nets=None):
        self.calls: list[list[str]] = []
        self.nets = nets or {}

    def __call__(self, *args):
        self.calls.append(list(args))
        if args[:2] == ("network", "create"):
            return SimpleNamespace(returncode=0, stdout="netid\n", stderr="")
        if args[0] == "inspect":
            import json as _json
            nets = {name: {"IPAddress": ip} for name, ip in self.nets.items()}
            return SimpleNamespace(
                returncode=0,
                stdout=_json.dumps([{"NetworkSettings": {"Networks": nets}}]),
                stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture()
def stub_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "server.py").write_text(MARKER_SERVER)
    (ws / "data.txt").write_text("ws-one\n")
    return ws


def _runner(ws, run_id="run-1", **overrides):
    params = dict(image="agent-sandbox:1.0", log_file=ws / "server.log")
    params.update(overrides)
    return DockerServerRunner(str(ws), run_id, **params)


def test_server_argv_confinement(stub_ws, monkeypatch):
    monkeypatch.setattr(sandbox_module, "verify_image", lambda image: None)
    runner = _runner(stub_ws)
    argv = runner.build_argv()
    joined = " ".join(argv)
    assert "--network" in argv and runner.network in argv
    assert runner.network.startswith("agent-verify-net-")
    assert f"{stub_ws}:/app:ro" in argv
    assert "--read-only" in argv and "--user" in argv and "nobody" in argv
    for flag in ("--memory", "--memory-swap", "--cpus", "--pids-limit",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--tmpfs"):
        assert flag in argv, flag
    assert " -p " not in f" {joined} " and "--publish" not in argv
    assert "OPENAI_API_KEY" not in joined and "APPROVAL_TOKEN" not in joined
    assert "docker.sock" not in joined
    assert argv[-3:] == ["sh", "-c", argv[-1]] and "server.py" in argv[-1]


def test_per_run_network_and_container_unique(stub_ws):
    assert _runner(stub_ws, "run-aaa").network != _runner(stub_ws, "run-bbb").network
    assert _runner(stub_ws, "run-aaa").container != _runner(stub_ws, "run-bbb").container


def test_start_stop_lifecycle_owns_only_itself(stub_ws, monkeypatch):
    monkeypatch.setattr(sandbox_module, "verify_image", lambda image: None)
    made = []
    runner = _runner(stub_ws, "run-aaa",
                     popen_factory=lambda *a, **k: made.append(FakePopen(*a, **k)) or made[-1])
    docker = FakeDocker({runner.network: "172.19.0.9"})
    monkeypatch.setattr(sandbox_module, "_docker", docker)
    monkeypatch.setattr(sandbox_module, "_http_ok", lambda url: True)
    assert runner.start() == "http://172.19.0.9:8080"
    assert made and made[0].kwargs.get("env", {}) == {
        "PATH": "/usr/local/bin:/usr/bin:/bin"}
    other = _runner(stub_ws, "run-bbb")
    runner.stop()
    rm_nets = [c for c in docker.calls if c[:2] == ["network", "rm"]]
    rm_containers = [c for c in docker.calls if c[:2] == ["rm", "-f"]]
    assert [[runner.network]] == [c[2:] for c in rm_nets]
    assert [[runner.container]] == [c[2:] for c in rm_containers]
    assert other.network not in str(rm_nets) and other.container not in str(rm_containers)


def test_unhealthy_server_raises_after_cleanup(stub_ws, monkeypatch):
    monkeypatch.setattr(sandbox_module, "verify_image", lambda image: None)
    docker = FakeDocker()
    monkeypatch.setattr(sandbox_module, "_docker", docker)
    monkeypatch.setattr(sandbox_module, "_http_ok", lambda url: False)
    monkeypatch.setattr(sandbox_module, "SERVER_START_TIMEOUT_SEC", 0)
    runner = _runner(stub_ws, popen_factory=lambda *a, **k: FakePopen(*a, **k))
    with pytest.raises(ServerError):
        runner.start()
    assert any(c[:2] == ["network", "rm"] for c in docker.calls)


def test_digest_checked_before_launch(stub_ws, monkeypatch):
    seen = []
    monkeypatch.setattr(
        sandbox_module, "verify_image",
        lambda image: seen.append(image) or (_ for _ in ()).throw(
            SandboxUnavailable("not built")))
    runner = _runner(stub_ws, popen_factory=lambda *a, **k: FakePopen(*a, **k))
    with pytest.raises(SandboxUnavailable):
        runner.start()
    assert seen == ["agent-sandbox:1.0"]


def test_browser_launch_uses_scrubbed_env(monkeypatch):
    import asyncio

    monkeypatch.setenv("OPENAI_API_KEY", "sk-trap-1234567890abcdef")
    monkeypatch.setenv("APPROVAL_TOKEN", "trap-token-1234567890abcdef")
    launched: dict = {}

    class FakePage:
        def on(self, *a):
            pass

    class FakeBrowser:
        async def new_page(self, **kwargs):
            return FakePage()

        async def close(self):
            pass

    class FakeChromium:
        async def launch(self, **kwargs):
            launched.update(kwargs)
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()

        async def start(self):
            return self

        async def stop(self):
            pass

    def fake_playwright():
        return FakePW()

    monkeypatch.setattr("app.tools.browser.async_playwright", fake_playwright)
    env = browser_env()
    assert set(env) == {"PATH", "HOME", "LANG", "TZ"}
    assert "OPENAI_API_KEY" not in env and "APPROVAL_TOKEN" not in env

    async def run():
        tool = BrowserTool()
        await tool.start()
        await tool.close()

    asyncio.run(run())
    assert launched.get("headless") is True
    assert launched.get("env") == env
    assert "--no-sandbox" not in str(launched)


def test_server_launch_failure_leaves_fix_unverified(monkeypatch):
    import asyncio

    from app.services.orchestrator import Orchestrator
    from app.services.session import session_store

    def _boom():
        raise ServerError("isolated fixture server stayed unhealthy")

    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.fix_worktree = "/home/manishbhaktisagar/Downloads/ai-test-automation-agent"
    session = session_store.create(run.run_id)
    try:
        monkeypatch.setattr(
            "app.services.orchestrator.DockerServerRunner",
            lambda *a, **k: SimpleNamespace(start=_boom, stop=lambda: None))
        with pytest.raises(ServerError):
            asyncio.run(orch._verify_fix(run, session))
        assert run.fix_status != "verified"
    finally:
        session_store.sessions.pop(run.run_id, None)


@pytest.mark.skipif(not sandbox_module.docker_available(),
                    reason="needs a local Docker daemon")
def test_live_isolation_smoke(tmp_path):
    """Observed (not assumed): host reaches the app; the app reaches
    nothing — no egress, no host loopback, no gateway services — and the
    served content comes from the mounted worktree."""
    import urllib.request

    from app.tools.sandbox import DockerServerRunner as RealRunner

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "server.py").write_text(MARKER_SERVER)
    (ws / "data.txt").write_text("smoke-marker\n")
    runner = RealRunner(str(ws), "smoke-run-1", image=settings.sandbox_image,
                        log_file=tmp_path / "server.log")
    base = runner.start()
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as resp:
            assert resp.status == 200
            assert "marker:smoke-marker" in resp.read().decode()
        assert "agent-verify-net-" in runner.network
        container = runner.container
        # Egress fails (DNS + routing blocked on the internal network).
        out = subprocess.run(
            ["docker", "exec", container, "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://example.com', timeout=8)"],
            capture_output=True, text=True, timeout=30)
        assert out.returncode != 0
        # Host loopback is a different network namespace.
        out = subprocess.run(
            ["docker", "exec", container, "python", "-c",
             "import socket; socket.create_connection(('127.0.0.1', 8001), timeout=5)"],
            capture_output=True, text=True, timeout=30)
        assert out.returncode != 0
        # No host env, no docker socket inside.
        out = subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             "env | grep -cE 'OPENAI_API_KEY|APPROVAL_TOKEN'; ls /var/run/docker.sock"],
            capture_output=True, text=True, timeout=30)
        assert "0" in out.stdout and "No such file" in (out.stdout + out.stderr)
    finally:
        runner.stop()
    leftovers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={container}", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30).stdout
    assert container not in leftovers
    nets = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        capture_output=True, text=True, timeout=30).stdout
    assert runner.network not in nets
