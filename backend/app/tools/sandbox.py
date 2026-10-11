"""Test-execution sandbox: arbitrary workspace code runs containerized.

Interface first: anything running untrusted tests implements run_pytest
with the TestRunnerTool result shape. DockerSandboxRunner confines the
run (no network, unprivileged user, read-only root and workspace, tmpfs
scratch, hard resource caps, dropped capabilities, no new privileges,
scrubbed environment). LocalRunner is the same code path as ever but is
only selectable through the explicit development opt-in
ALLOW_INSECURE_LOCAL_EXEC — it warns loudly because it isolates nothing.

Selection rule (select_runner): Docker when enabled and the daemon
answers; insecure-local only on explicit opt-in; otherwise
SandboxUnavailable — fail closed, never silently downgrade.
"""

import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from ..config import settings
from .codebase import redact_secrets
from .testrunner import TestRunnerTool

DOCKER_TIMEOUT_SEC = 180
MAX_OUTPUT_CHARS = 8000
_POLL_SEC = 0.2


class SandboxUnavailable(RuntimeError):
    """No safe backend for untrusted test execution right now."""


class ServerError(RuntimeError):
    """The isolated fixture server failed to start or stayed unhealthy."""


# Inside the container the fixture keeps its own port (it binds
# 127.0.0.1); the baked-in relay exposes it on all container interfaces
# at RELAY_PORT without touching the fixture's bind behavior.
SERVER_PORT = 8921
RELAY_PORT = 8080
SERVER_START_TIMEOUT_SEC = 25


def _docker(*args: str):
    """One Docker CLI call with a controlled environment (PATH only)."""
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )


def _http_ok(url: str) -> bool:
    """Health probe for test doubles to replace (no real sockets in unit)."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except OSError:
        return False


class ServerRunner:
    """Interface: a fixture server serving exactly one run's worktree."""

    base_url: str = ""

    def start(self) -> str:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class DockerServerRunner(ServerRunner):
    """Fixture server in its own --internal Docker network.

    Topology (validated, not assumed): one internal network per run, one
    container, no published ports. The host reaches the app at the
    container's bridge IP directly; the container cannot route outward
    (DNS/egress fail), cannot reach host loopback (separate netns), and
    cannot reach host services through the gateway (they bind 127.0.0.1).
    The worktree mounts read-only; the image digest is verified first.
    """

    def __init__(
        self, workspace: str | Path, run_id: str, image: str,
        memory: str = "512m", cpus: str = "1.0", pids_limit: int = 64,
        log_file: str | Path | None = None, popen_factory=None,
    ):
        self.workspace = Path(workspace).resolve()
        self.run_id = run_id
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.log_file = Path(log_file) if log_file else None
        self._popen = popen_factory or subprocess.Popen
        short = run_id.replace("-", "")[:12]
        self.network = f"agent-verify-net-{short}"
        self.container = f"agent-verify-srv-{short}"
        self.base_url = ""

    def build_argv(self) -> list[str]:
        return [
            "docker", "run", "-d", "--rm",
            "--name", self.container,
            "--network", self.network,
            "--user", "nobody",
            "--read-only",
            "-v", f"{self.workspace}:/app:ro",
            "--tmpfs", "/tmp:rw,size=64m,noexec",
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "LANG=C.UTF-8",
            "-e", "HOME=/tmp",
            "-w", "/app",
            self.image,
            "sh", "-c",
            f"python /relay.py {RELAY_PORT} {SERVER_PORT} & "
            f"exec python server.py {SERVER_PORT}",
        ]

    def _container_ip(self) -> str:
        # Straight to JSON: Go templates choke on dashes in network names.
        import json as _json
        proc = _docker("inspect", self.container)
        try:
            nets = _json.loads(proc.stdout)[0]["NetworkSettings"]["Networks"]
            ip = nets[self.network]["IPAddress"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ServerError("could not discover server container IP")
        if not ip:
            raise ServerError("server container has no IP on its network")
        return ip

    def start(self) -> str:
        verify_image(self.image)
        net = _docker("network", "create", "--internal", self.network)
        if net.returncode != 0:
            raise ServerError(f"verify network creation failed: {net.stderr.strip()[-200:]}")
        try:
            proc = self._popen(
                self.build_argv(), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            rc = proc.wait(timeout=30)
            if rc != 0:
                out = proc.stdout.read() if proc.stdout else ""
                raise ServerError(f"server container exited: {out[-300:]}")
            ip = self._container_ip()
            self.base_url = f"http://{ip}:{RELAY_PORT}"
            deadline = time.monotonic() + SERVER_START_TIMEOUT_SEC
            while time.monotonic() < deadline:
                if _http_ok(self.base_url + "/"):
                    return self.base_url
                time.sleep(0.5)
            raise ServerError("isolated fixture server stayed unhealthy")
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        """Remove only this run's container and network. Never raises:
        cleanup must not flip a verification verdict. Container logs are
        preserved to the log file first (best effort)."""
        if self.log_file is not None:
            try:
                logs = _docker("logs", self.container)
                if logs.returncode == 0 and logs.stdout:
                    with open(self.log_file, "a", encoding="utf-8") as fh:
                        fh.write(logs.stdout[-4000:])
            except Exception:
                pass
        try:
            _docker("rm", "-f", self.container)
        except Exception:
            pass
        try:
            _docker("network", "rm", self.network)
        except Exception:
            pass


class SandboxRunner:
    """Interface: run a workspace pytest suite in confinement."""

    def run_pytest(
        self, targets: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        raise NotImplementedError


def _pinned_digest() -> str | None:
    """Digest recorded by scripts/setup-sandbox.sh at build time, if any."""
    try:
        return (Path(__file__).resolve().parent.parent
                / "sandbox" / "IMAGE_DIGEST").read_text().strip() or None
    except OSError:
        return None


def verify_image(image: str) -> None:
    """Prove the exact image exists locally before running it.

    A missing image raises instead of pulling (no unpinned image can slip
    in at runtime); when the setup script recorded a digest, the local
    image must match it, otherwise a swapped tag is refused.
    """
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format",
             "{{.Id}} {{json .RepoDigests}}", image],
            capture_output=True, timeout=30,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable(f"sandbox image check failed: {exc}")
    if proc.returncode != 0:
        raise SandboxUnavailable(
            f"sandbox image {image} not built locally; "
            "run scripts/setup-sandbox.sh (images are never pulled at runtime)")
    pinned = _pinned_digest()
    if pinned and pinned not in proc.stdout:
        raise SandboxUnavailable(
            f"sandbox image {image} does not match the recorded build digest")


def docker_available() -> bool:
    """Daemon reachable? No caching: called once per fix flow, and tests
    fake the probe instead of needing Docker."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def select_runner(root: str | Path, timeout: int, on_insecure=None) -> SandboxRunner:
    """Choose the test backend. Docker preferred; insecure-local only on
    explicit opt-in with a caller-visible warning; otherwise fail closed."""
    root = Path(root).resolve()
    if settings.sandbox_enabled and docker_available():
        return DockerSandboxRunner(
            root, image=settings.sandbox_image, timeout=timeout,
            memory=settings.sandbox_memory, cpus=settings.sandbox_cpus,
            pids_limit=settings.sandbox_pids_limit,
        )
    if settings.allow_insecure_local_exec:
        if on_insecure is not None:
            on_insecure(
                "INSECURE local test execution: no filesystem, network, or "
                "resource isolation. Development only."
            )
        return LocalRunner(root, timeout=timeout)
    raise SandboxUnavailable(
        "docker unavailable and ALLOW_INSECURE_LOCAL_EXEC is not set; "
        "refusing to execute workspace tests on the host. Run "
        "scripts/setup-sandbox.sh or enable the explicit dev override."
    )


class LocalRunner(TestRunnerTool, SandboxRunner):
    """Development-only fallback: host execution with a scrubbed env but
    NONE of Docker's isolation. Only constructed via the explicit opt-in."""


def _await_proc(proc, timeout: int, cancel_event, container: str | None = None):
    """Wait with timeout + cooperative cancel; always reap. On cancel or
    timeout of a container, force-remove it so nothing is orphaned."""
    deadline = time.monotonic() + timeout
    cancelled = False
    timed_out = False
    out = ""
    try:
        while True:
            try:
                out, _ = proc.communicate(timeout=_POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
    finally:
        if (cancelled or timed_out) and container is not None:
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True,
                timeout=30, env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()
    return out or "", proc.returncode, cancelled, timed_out


class DockerSandboxRunner(SandboxRunner):
    """pytest inside a locked-down container. The image must already exist
    locally (scripts/setup-sandbox.sh builds and verifies it) — the runner
    never pulls, so no unpinned image can slip in at runtime."""

    def __init__(
        self, root: str | Path, image: str, timeout: int = DOCKER_TIMEOUT_SEC,
        memory: str = "512m", cpus: str = "1.0", pids_limit: int = 64,
        popen_factory=None,
    ):
        self.root = Path(root).resolve()
        self.image = image
        self.timeout = timeout
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self._popen = popen_factory or subprocess.Popen

    def build_argv(self, container: str, targets: list[str] | None) -> list[str]:
        return [
            "docker", "run", "--rm",
            "--name", container,
            "--network", "none",
            "--user", "nobody",
            "--read-only",
            "-v", f"{self.root}:/work:ro",
            "--tmpfs", "/tmp:rw,size=64m,noexec",
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "LANG=C.UTF-8",
            "-e", "HOME=/tmp",
            "-w", "/work",
            self.image,
            "python", "-m", "pytest", *(targets or []), "-q",
            "-p", "no:cacheprovider",
        ]

    def run_pytest(
        self, targets: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        try:
            verify_image(self.image)
        except SandboxUnavailable as exc:
            return {"passed": False, "returncode": -1, "cancelled": False,
                    "output": str(exc)}
        container = f"agent-test-{uuid.uuid4().hex[:12]}"
        argv = self.build_argv(container, targets)
        try:
            proc = self._popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
        except OSError as exc:
            return {"passed": False, "returncode": -1, "cancelled": False,
                    "output": f"sandbox launch failed: {exc}"}
        out, returncode, cancelled, timed_out = _await_proc(
            proc, self.timeout, cancel_event, container)
        output = redact_secrets(out[-MAX_OUTPUT_CHARS:])
        if timed_out:
            return {"passed": False, "returncode": -1, "cancelled": False,
                    "output": f"sandbox pytest timed out after {self.timeout}s\n{output}"}
        if cancelled:
            output += "\n[test run cancelled by operator]"
        return {"passed": returncode == 0 and not cancelled,
                "returncode": returncode if returncode is not None else -1,
                "output": output, "cancelled": cancelled}
