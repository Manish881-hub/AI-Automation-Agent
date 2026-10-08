import json
import re
from typing import Type, TypeVar
from openai import AsyncOpenAI, BadRequestError
from pydantic import BaseModel
from ..config import settings

T = TypeVar("T", bound=BaseModel)


def _strip_fences(content: str) -> str:
    """Remove markdown code fences some models wrap JSON payloads in."""
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    return match.group(1).strip() if match else content.strip()


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

    async def structured(self, system: str, user: str, schema: Type[T]) -> T:
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
        except BadRequestError as exc:
            # Free-tier models that reject json_object mode: retry as plain
            # text and parse the JSON payload out of the reply instead of
            # failing the whole run on a provider capability gap.
            if "response_format" not in str(exc).lower():
                raise
            response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
            )
            content = _strip_fences(response.choices[0].message.content or "{}")
        return schema.model_validate(json.loads(content))

    async def text(self, system: str, user: str) -> str:
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
