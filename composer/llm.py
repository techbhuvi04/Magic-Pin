"""Optional LLM client — OpenAI or Anthropic when keys present; else None."""

from __future__ import annotations

import json
import os
import re
from typing import Optional


class LLMClient:
    def __init__(self) -> None:
        self.provider: Optional[str] = None
        self.model: str = "template"
        self._client = None
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if openai_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=openai_key)
                self.provider = "openai"
                self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            except Exception:
                pass
        elif anthropic_key:
            try:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=anthropic_key)
                self.provider = "anthropic"
                self.model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._client is not None and self.provider is not None

    def complete(self, system: str, user: str) -> str:
        if not self.available:
            raise RuntimeError("No LLM configured")
        if self.provider == "openai":
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content or ""
        # anthropic
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=800,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text if resp.content else ""

    def complete_json(self, system: str, user: str) -> dict:
        text = self.complete(system, user)
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return {}


_llm: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
