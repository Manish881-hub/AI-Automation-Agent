"""Explicit minimal environments for host-launched subprocesses.

Nothing launched by the agent inherits the host environment implicitly:
no OPENAI_API_KEY, no APPROVAL_TOKEN, no operator secrets of any kind.
Each entry point declares the small set it genuinely needs (tool lookup,
home-dir git identity, locale). Anything else must be passed explicitly
at the call site — and audited.
"""

import os


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """PATH/HOME/LANG baseline plus caller-supplied additions only."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.path.expanduser("~"),
        "LANG": "C.UTF-8",
    }
    if extra:
        env.update(extra)
    return env


def pytest_env() -> dict[str, str]:
    """Test execution: minimal baseline, bytecode writes off so read-only
    checkouts stay runnable."""
    return minimal_env({"PYTHONDONTWRITEBYTECODE": "1"})
