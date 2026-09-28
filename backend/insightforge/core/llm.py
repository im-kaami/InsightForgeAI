import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from openai import OpenAI

from insightforge.config import Settings, get_settings, is_loopback_url

PROVIDER_PRESETS: dict[str, tuple[str | None, str]] = {
    "openai": (None, "gpt-4o-mini"),
    "openai-compatible": (None, "gpt-4o-mini"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.6-flash"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
    "fake": (None, "gpt-4o-mini"),
}


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMJSONError(ValueError):
    pass


def describe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


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
        return _parse_json_response(response), response


def _parse_json_response(response: LLMResponse) -> Any:
    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError:
        parsed = extract_json(response.text)
        if parsed is not None:
            logging.getLogger("insightforge").warning(
                "LLM returned non-strict JSON; extracted embedded object"
            )
    if parsed is None:
        raise LLMJSONError("LLM response did not contain valid JSON")
    return parsed


class OllamaClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        context_tokens: int = 8192,
        think: bool = False,
        timeout: float = 300.0,
        max_output_tokens: int = 1024,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.context_tokens = context_tokens
        self.think = think
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    def _chat(self, messages: list[dict[str, str]], temperature: float, json_mode: bool) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": {
                "num_ctx": self.context_tokens,
                "num_predict": self.max_output_tokens,
                "temperature": temperature,
            },
        }
        if json_mode:
            payload["format"] = "json"
        with httpx.Client(trust_env=False, timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 or "error" in data:
            detail = str(data.get("error") or f"HTTP {response.status_code}")[:300]
            raise RuntimeError(f"Local model request failed: {detail}")
        return LLMResponse(
            text=(data.get("message") or {}).get("content") or "",
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> LLMResponse:
        return self._chat(messages, temperature, json_mode=False)

    def chat_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.0
    ) -> tuple[Any, LLMResponse]:
        response = self._chat(messages, temperature, json_mode=True)
        return _parse_json_response(response), response


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


def offline_fake_llm() -> FakeLLMClient:
    def respond(messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        if "Correct the DuckDB SQL" in system:
            match = re.search(r"Query:\s*(.*?)\s*\n\nError:", user, re.DOTALL)
            return json.dumps({"query": match.group(1) if match else "SELECT 1"})
        if "data-analysis planner" in system:
            from insightforge.core.planner import fallback_plan
            from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo

            tables: list[TableInfo] = []
            matches = list(re.finditer(r"^TABLE (.+) \((\d+) rows\)$", system, re.MULTILINE))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(system)
                block = system[match.end() : end]
                columns = [
                    ColumnInfo(name=name.strip(), dtype=dtype.split("  e.g.", 1)[0].strip())
                    for name, dtype in re.findall(r"^- ([^:]+): (.+)$", block, re.MULTILINE)
                ]
                tables.append(
                    TableInfo(name=match.group(1), row_count=int(match.group(2)), columns=columns)
                )
            return fallback_plan(user, SchemaInfo(tables=tables)).model_dump_json()
        names = re.findall(r"^Table: ([^\n]+)", user, re.MULTILINE)
        listed = ", ".join(names) if names else "the available results"
        return f"## Summary\n\nOffline analysis completed for {listed}."

    return FakeLLMClient(respond)


def llm_mode(client: LLMClient) -> Literal["fake", "openai"]:
    return "fake" if isinstance(client, FakeLLMClient) else "openai"


def resolved_base_url(settings: Settings) -> str | None:
    return settings.llm_base_url or PROVIDER_PRESETS[settings.llm_provider][0]


def resolved_model(settings: Settings) -> str:
    return settings.llm_model or PROVIDER_PRESETS[settings.llm_provider][1]


def build_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider == "fake":
        return offline_fake_llm()
    base_url = resolved_base_url(settings)
    model = resolved_model(settings)
    if settings.llm_provider == "openai-compatible" and not base_url:
        raise ValueError("LLM_BASE_URL is required for the openai-compatible provider")
    api_key = settings.llm_api_key
    if api_key is None:
        if settings.llm_provider == "ollama":
            api_key = "ollama"
        elif base_url:
            api_key = "not-needed"
        else:
            logging.getLogger("insightforge").warning(
                "LLM_API_KEY is not set; running in offline fake mode "
                "(set LLM_API_KEY or LLM_PROVIDER=fake to silence this)"
            )
            return offline_fake_llm()
    return OpenAICompatibleClient(model, api_key, base_url)


def build_local_llm(settings: Settings | None = None) -> LLMClient | None:
    settings = settings or get_settings()
    if not settings.local_llm_model or not is_loopback_url(settings.local_llm_base_url):
        return None
    return OllamaClient(
        settings.local_llm_model,
        settings.local_llm_base_url,
        context_tokens=settings.local_llm_context_tokens,
        think=settings.local_llm_think,
        timeout=settings.local_llm_timeout_seconds,
        max_output_tokens=settings.local_llm_max_output_tokens,
    )
