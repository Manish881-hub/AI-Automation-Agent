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
    # White-box fix loop: workspace the codebase tools may inspect.
    # Writes apply only through the human approval gate.
    workspace_root: str = "."
    fix_enabled: bool = True
    test_timeout_sec: int = 180
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
