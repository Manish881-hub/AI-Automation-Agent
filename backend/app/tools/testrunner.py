"""Run automated tests as a controlled capability: pytest only, no shells.

The agent can verify a fix, never execute arbitrary commands. Output is
capped and secret-redacted before it reaches any LLM context. Cancellation
is cooperative: pass the run's cancel event and a long test run is
terminated and reaped instead of running out the clock.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

from .codebase import redact_secrets
from .environ import pytest_env

TEST_TIMEOUT_SEC = 180
MAX_OUTPUT_CHARS = 8000
_POLL_SEC = 0.2


class TestRunnerTool:
    def __init__(self, root: str | Path, timeout: int = TEST_TIMEOUT_SEC):
        self.root = Path(root).resolve()
        self.timeout = timeout

    def run_pytest(
        self, targets: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Run `pytest <targets> -q` under the workspace root.

        Returns {"passed", "returncode", "output", "cancelled"}. Failure is
        data, not an exception; cancellation is reported explicitly so the
        orchestrator never mistakes a killed run for a failed suite. The
        process is always reaped — no orphaned pytest survives this call.
        """
        cmd = [sys.executable, "-m", "pytest", *(targets or []), "-q"]
        proc = subprocess.Popen(
            cmd, cwd=self.root, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            env=pytest_env(),  # never the host environment: no secrets leak in
        )
        deadline = time.monotonic() + self.timeout
        cancelled = False
        try:
            while True:
                try:
                    out, _ = proc.communicate(timeout=_POLL_SEC)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        proc.terminate()
                        try:
                            out, _ = proc.communicate(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            out, _ = proc.communicate()
                        cancelled = True
                        break
                    if time.monotonic() >= deadline:
                        proc.kill()
                        out, _ = proc.communicate()
                        return {
                            "passed": False, "returncode": -1, "cancelled": False,
                            "output": f"pytest timed out after {self.timeout}s",
                        }
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        output = redact_secrets((out or "")[-MAX_OUTPUT_CHARS:])
        if cancelled:
            output += "\n[test run cancelled by operator]"
        return {
            "passed": proc.returncode == 0 and not cancelled,
            "returncode": proc.returncode if proc.returncode is not None else -1,
            "output": output,
            "cancelled": cancelled,
        }
