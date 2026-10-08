import json
from typing import Type, TypeVar
from openai import AsyncOpenAI
from pydantic import BaseModel
from ..config import settings

T = TypeVar("T", bound=BaseModel)


class LLM:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def structured(self, system: str, user: str, schema: Type[T]) -> T:
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
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
