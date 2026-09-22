import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI

from insightforge.config import Settings, get_settings


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMJSONError(ValueError):
    pass


class LLMClient(Protocol):
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> LLMResponse: ...

    def chat_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> tuple[Any, LLMResponse]: ...


def extract_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        pass
    for match in re.finditer(r"[\[{]", text):
        opener = match.group()
        closer = "]" if opener == "[" else "}"
        depth = 0
        in_string = False
        escaped = False
        for index in range(match.start(), len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[match.start() : index + 1])
                    except json.JSONDecodeError:
                        break
    return None


class OpenAICompatibleClient:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _create(self, messages: list[dict[str, str]], temperature: float, **kwargs: Any) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature, **kwargs
        )
        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> LLMResponse:
        return self._create(messages, temperature)

    def chat_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> tuple[Any, LLMResponse]:
        try:
            response = self._create(messages, temperature, response_format={"type": "json_object"})
        except Exception:
            response = self._create(messages, temperature)
        parsed = extract_json(response.text)
        if parsed is None:
            raise LLMJSONError("LLM response did not contain valid JSON")
        return parsed, response


class FakeLLMClient:
    def __init__(self, responses: list[str] | Callable[[list[dict[str, str]]], str]):
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []
        self._index = 0

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> LLMResponse:
        del temperature
        copied = [dict(message) for message in messages]
        self.calls.append(copied)
        if callable(self.responses):
            text = self.responses(copied)
        else:
            if not self.responses:
                raise ValueError("FakeLLMClient requires at least one response")
            index = min(self._index, len(self.responses) - 1)
            text = self.responses[index]
            self._index += 1
        return LLMResponse(text=text)

    def chat_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> tuple[Any, LLMResponse]:
        response = self.chat(messages, temperature=temperature)
        parsed = extract_json(response.text)
        if parsed is None:
            raise LLMJSONError("LLM response did not contain valid JSON")
        return parsed, response


def build_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    return OpenAICompatibleClient(settings.llm_model, settings.llm_api_key, settings.llm_base_url)
