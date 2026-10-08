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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
