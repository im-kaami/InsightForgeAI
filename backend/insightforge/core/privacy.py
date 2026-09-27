from dataclasses import dataclass
from typing import Literal

from insightforge.core.llm import LLMClient, offline_fake_llm
from insightforge.core.memory import ConversationMemory
from insightforge.core.schema import SchemaInfo

PrivacyMode = Literal["local", "schema_only", "full"]


@dataclass(frozen=True)
class PromptPolicy:
    mode: PrivacyMode = "local"

    def __post_init__(self) -> None:
        if self.mode not in {"local", "schema_only", "full"}:
            raise ValueError("Unknown data-sharing policy")

    def client(self, configured: LLMClient) -> LLMClient:
        return offline_fake_llm() if self.mode == "local" else configured

    def schema_text(self, schema: SchemaInfo) -> str:
        if self.mode in {"full", "local"}:
            return schema.to_prompt()
        blocks = []
        remaining = 60
        for table in schema.tables[:20]:
            lines = [f"TABLE {table.name}"]
            for column in table.columns[:remaining]:
                sensitivity = f" [sensitive: {column.sensitivity}]" if column.sensitivity else ""
                lines.append(f"- {column.name}: {column.dtype}{sensitivity}")
            remaining = max(0, remaining - len(table.columns))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def memory_text(self, memory: ConversationMemory | None) -> str:
        if not memory:
            return ""
        if self.mode in {"local", "full"}:
            return memory.to_prompt()
        return "\n".join(f"Previous user question: {turn.goal}" for turn in memory.turns)

    def repair_error(self, error: str) -> str:
        if self.mode == "full":
            return error
        return "SQL execution failed. Recheck identifiers, types, grouping and DuckDB syntax."

    @property
    def remote_summary_allowed(self) -> bool:
        return self.mode == "full"
