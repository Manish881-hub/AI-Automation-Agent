import asyncio
import json
import re
from typing import Any, Type, TypeVar
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel
from ..config import settings
from ..tools.codebase import redact_secrets

T = TypeVar("T", bound=BaseModel)

# HTTP statuses worth one more attempt (shared-pool 429s, bad gateways,
# slow upstreams). Anything else — auth, validation, not-found — fails
# fast so a bad key or bad request surfaces instead of burning budget.
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _strip_fences(content: str) -> str:
    """Remove markdown code fences some models wrap JSON payloads in."""
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    return match.group(1).strip() if match else content.strip()


def candidate_models() -> list[str]:
    """Primary plus configured OpenRouter fallbacks, in priority order."""
    fallbacks = [m.strip() for m in settings.openai_fallback_models.split(",")]
    return [settings.openai_model] + [m for m in fallbacks if m]


def is_transient(exc: BaseException) -> bool:
    """True for rate limits and shaky-provider errors; False for auth,
    permission, validation, and not-found errors that retrying cannot fix."""
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError,
                        InternalServerError)):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(exc, APIStatusError) and status in TRANSIENT_STATUSES


def retry_delay_sec(exc: BaseException, attempt: int) -> float:
    """Honor Retry-After when the provider sends one, else exponential
    backoff. Always capped so a hostile header cannot stall a run."""
    try:
        raw = exc.response.headers.get("retry-after")  # type: ignore[union-attr]
        if raw is not None:
            return max(0.0, min(float(raw), settings.llm_retry_max_sec))
    except (AttributeError, TypeError, ValueError):
        pass
    return min(settings.llm_retry_base_sec * (2 ** attempt), settings.llm_retry_max_sec)


def is_infrastructure_error(exc: BaseException) -> bool:
    """Provider/transport/timeout failures are infrastructure: the website
    under test was never judged. Anything else (bad plan, schema surprise,
    agent bug) is an agent failure — same terminal handling, honest kind."""
    if isinstance(exc, (APITimeoutError, APIConnectionError, APIStatusError)):
        return True
    msg = str(exc).lower()
    return "timed out" in msg or "budget" in msg


def describe_provider_error(exc: BaseException) -> str:
    """Short, actionable, secret-free message for the run timeline.

    The raw provider blob (see the Novita 429 envelope) is huge and
    unactionable in a dashboard; report what happened, whose fault it is
    (infrastructure, not the website under test), and what to do next.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(exc, RateLimitError) or status == 429:
        return (
            "LLM provider rate limit (HTTP 429): the model backend is "
            "temporarily out of shared capacity — the website under test "
            "did not fail. Retry shortly, or set OPENAI_FALLBACK_MODELS "
            "to route around the limited provider."
        )
    if isinstance(exc, APITimeoutError):
        return (
            "LLM request timed out: the model backend did not answer in "
            "time. The website under test did not fail; retry the run."
        )
    if isinstance(exc, APIConnectionError) or status in (502, 503, 504):
        return (
            f"LLM provider unavailable (HTTP {status or 'connection error'}): "
            "the model backend errored before answering. The website under "
            "test did not fail; retry shortly."
        )
    if status in (500, 408):
        return (
            f"LLM provider error (HTTP {status}): transient upstream "
            "failure after bounded retries. The website under test did "
            "not fail; retry the run."
        )
    return redact_secrets(str(exc))[:300] or type(exc).__name__


class LLM:
    def __init__(self):
        if not settings.openai_api_key:
            self.client = None
            return
        kwargs: dict = {
            "api_key": settings.openai_api_key,
            "base_url": settings.openai_base_url,
        }
        if "openrouter" in settings.openai_base_url:
            # OpenRouter attribution headers (optional, ignored elsewhere).
            kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/Manish881-hub/AI-Automation-Agent",
                "X-Title": "AI Test Automation Agent",
            }
        self.client = AsyncOpenAI(**kwargs)

    def _extra_body(self) -> dict[str, Any]:
        """OpenRouter-native fallback routing via the OpenAI SDK's
        supported extra_body mechanism. Non-OpenRouter backends ignore it
        (no fallbacks sent), so this never changes plain-OpenAI behavior."""
        if "openrouter" not in settings.openai_base_url:
            return {}
        models = candidate_models()
        return {"models": models} if len(models) > 1 else {}

    async def _create(self, messages: list[dict], json_mode: bool) -> str:
        """One SDK attempt, including the json_object capability fallback
        for models that reject response_format (provider capability gap,
        not a reason to fail the run)."""
        extra = self._extra_body()
        create_kwargs: dict[str, Any] = {
            "model": settings.openai_model,
            "messages": messages,
        }
        if extra:
            create_kwargs["extra_body"] = extra
        try:
            if json_mode:
                create_kwargs["response_format"] = {"type": "json_object"}
            response = await self.client.chat.completions.create(**create_kwargs)
            return response.choices[0].message.content or "{}"
        except BadRequestError as exc:
            # Free-tier models that reject json_object mode: retry as plain
            # text and parse the JSON payload out of the reply instead of
            # failing the whole run on a provider capability gap.
            if "response_format" not in str(exc).lower():
                raise
            create_kwargs.pop("response_format", None)
            response = await self.client.chat.completions.create(**create_kwargs)
            return _strip_fences(response.choices[0].message.content or "{}")

    async def _create_resilient(self, messages: list[dict], json_mode: bool) -> str:
        """Bounded retries over transient provider errors.

        Total SDK attempts never exceed llm_max_attempts; the last error
        propagates so the orchestrator can record a terminal failure with
        the failed stage instead of hanging or crashing.
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        max_attempts = max(1, settings.llm_max_attempts)
        last_exc: BaseException | None = None
        for attempt in range(max_attempts):
            try:
                return await self._create(messages, json_mode)
            except Exception as exc:
                if not is_transient(exc) or attempt + 1 >= max_attempts:
                    raise
                last_exc = exc
                await asyncio.sleep(retry_delay_sec(exc, attempt))
        assert last_exc is not None
        raise last_exc

    async def structured(self, system: str, user: str, schema: Type[T]) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        content = await self._create_resilient(messages, json_mode=True)
        return schema.model_validate(json.loads(content))

    async def text(self, system: str, user: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self._create_resilient(messages, json_mode=False)
