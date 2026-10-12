from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    # Any OpenAI-compatible endpoint. Point at OpenRouter to use free
    # models, e.g. OPENAI_BASE_URL=https://openrouter.ai/api/v1 with an
    # OpenRouter key and OPENAI_MODEL=<vendor/model:free>.
    openai_base_url: str = "https://api.openai.com/v1"
    artifact_dir: str = "./artifacts"
    cors_origins: str = "http://localhost:5173"
    test_email: str = "test@example.com"
    test_password: str = "Test1234!"
    # White-box fix loop: default workspace the codebase tools may inspect.
    # Writes apply only through the human approval gate.
    workspace_root: str = "."
    # Origin served by the default workspace app (used to refuse proposals
    # for runs targeting a different application — the fixer must never
    # patch app B for app A's failure).
    workspace_origin: str = "http://localhost:8921"
    # Named extra workspaces: {"name": {"root": "/abs/path", "origin":
    # "http://app-origin"}}. Select per run via the `workspace` request
    # field; unknown names are rejected. Both root (existing directory) and
    # origin (for the mismatch guard) are required — fail closed otherwise.
    workspace_allowlist: dict[str, dict[str, str]] = {}
    fix_enabled: bool = True
    test_timeout_sec: int = 180
    # Human-approval authorization: approve/reject require this value via
    # the X-Approval-Token header or an operator session cookie from
    # POST /api/auth/login. EMPTY FAILS CLOSED — decisions are refused
    # until a token is configured — because the gate decides whose code
    # gets applied, and an unconfigured server must never apply code.
    approval_token: str = ""
    # Test-execution sandbox. Docker is the only supported backend for
    # untrusted workspace code; local execution is a development-only,
    # explicitly insecure fallback (no filesystem/network isolation) that
    # stays disabled unless ALLOW_INSECURE_LOCAL_EXEC=true.
    sandbox_enabled: bool = True
    sandbox_image: str = "agent-sandbox:1.0"
    allow_insecure_local_exec: bool = False
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1.0"
    sandbox_pids_limit: int = 64
    # Browser verification launches the workspace's own server.py on the
    # host. True for the trusted local fixture; set False for untrusted
    # customer code — verification then refuses instead of executing it.
    trust_workspace_server: bool = True
    # OpenRouter fallback routing: comma-separated extra model ids tried by
    # OpenRouter itself when the primary is rate-limited or unavailable.
    # Sent via the OpenAI SDK's extra_body {"models": [...]} mechanism, so
    # no model names are hardcoded here — configure only models you have
    # verified, and prefer ones supporting JSON response_format (the
    # planner parses structured output; see the json_object fallback in
    # services/llm.py). Empty means primary-only.
    openai_fallback_models: str = ""
    # Bounded resilience for transient provider errors (429/502/503/504…):
    # total SDK attempts per LLM call, then the error propagates and the
    # orchestrator records a terminal infrastructure failure. Auth and
    # request-validation errors (401/403/400/404/422) are never retried.
    llm_max_attempts: int = 4
    llm_retry_base_sec: float = 1.0
    llm_retry_max_sec: float = 30.0
    # Per-run LLM budget (logical structured/text calls, not SDK attempts).
    # The pipeline's structural maximum is ~18 (plan + 2 revisions + up to
    # 12 recovery calls + debugger + 2 fix proposals); the default leaves
    # headroom so normal runs — including the golden autofix — never trip
    # it, while a looping agent stops loudly instead of spending freely.
    llm_max_calls_per_run: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def resolve_workspace(name: str | None) -> tuple[str, str, str, dict[str, str]]:
    """Resolve a run's workspace selection to (entry, root, origin, creds).

    None/"default" selects the configured default workspace. Named entries
    come only from the server-side allowlist — clients can never pass an
    arbitrary path — and require both an existing root directory and a
    declared origin, otherwise resolution fails closed. Optional
    test_email/test_password keys give the workspace a dedicated test
    account (never logged); without them the global demo credentials apply.
    """
    if not name or name == "default":
        return "default", settings.workspace_root, settings.workspace_origin, {}
    entry = settings.workspace_allowlist.get(name)
    if not isinstance(entry, dict) or not entry.get("root") or not entry.get("origin"):
        raise ValueError(f"unknown or incomplete workspace {name!r}")
    if not Path(entry["root"]).is_dir():
        raise ValueError(f"workspace {name!r} root is not a directory")
    creds = {
        token: secret for token, secret in (
            ("{{TEST_EMAIL}}", entry.get("test_email", "")),
            ("{{TEST_PASSWORD}}", entry.get("test_password", "")),
        ) if secret
    }
    return name, entry["root"], entry["origin"], creds
