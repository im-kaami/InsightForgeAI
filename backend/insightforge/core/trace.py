import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field

TraceKind = Literal["model", "sql", "chart", "check", "decision", "test", "code"]


class TraceEvent(BaseModel):
    step: str
    kind: TraceKind
    started_ms: float
    duration_ms: float
    ok: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class Tracer:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._origin = time.perf_counter()

    def record(self, step: str, kind: TraceKind, started: float, ok: bool = True, **details: Any) -> None:
        self.events.append(
            TraceEvent(
                step=step,
                kind=kind,
                started_ms=round((started - self._origin) * 1000, 1),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                ok=ok,
                details=details,
            )
        )

    @contextmanager
    def span(self, step: str, kind: TraceKind, **details: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        ok = True
        try:
            yield details
        except Exception as error:
            ok = False
            details["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            raise
        finally:
            self.record(step, kind, started, ok and "error" not in details, **details)


def model_label(client: Any) -> str:
    model = getattr(client, "model", None)
    return str(model) if model else "offline"


def prompt_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(message.get("content") or "") for message in messages)
