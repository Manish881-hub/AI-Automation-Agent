"""Run automated tests as a controlled capability: pytest only, no shells.

The agent can verify a fix, never execute arbitrary commands. Output is
capped and secret-redacted before it reaches any LLM context.
"""

import subprocess
import sys
from pathlib import Path

from .codebase import redact_secrets

TEST_TIMEOUT_SEC = 180
MAX_OUTPUT_CHARS = 8000


class TestRunnerTool:
    def __init__(self, root: str | Path, timeout: int = TEST_TIMEOUT_SEC):
        self.root = Path(root).resolve()
        self.timeout = timeout

    def run_pytest(self, targets: list[str] | None = None) -> dict:
        """Run `pytest <targets> -q` under the workspace root.

        Returns {"passed": bool, "returncode": int, "output": str}.
        Never raises on test failure — failure is data, not an exception.
        """
        cmd = [sys.executable, "-m", "pytest", *(targets or []), "-q"]
        try:
            proc = subprocess.run(
                cmd, cwd=self.root, capture_output=True, text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "passed": False, "returncode": -1,
                "output": f"pytest timed out after {self.timeout}s",
            }
        output = redact_secrets((proc.stdout + proc.stderr)[-MAX_OUTPUT_CHARS:])
        return {"passed": proc.returncode == 0, "returncode": proc.returncode, "output": output}
