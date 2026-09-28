from dataclasses import dataclass
from typing import Literal

from insightforge.core.llm import LLMClient, offline_fake_llm
from insightforge.core.memory import ConversationMemory
from insightforge.core.schema import SchemaInfo

PrivacyMode = Literal["local", "schema_only", "full"]


@dataclass(frozen=True)
class PromptPolicy:
    mode: PrivacyMode = "local"
    local_model: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"local", "schema_only", "full"}:
            raise ValueError("Unknown data-sharing policy")

    def client(self, configured: LLMClient, local: LLMClient | None = None) -> LLMClient:
        if self.mode == "local":
            return local if self.local_model and local is not None else offline_fake_llm()
        return configured

    @property
    def values_visible_to_model(self) -> bool:
        return self.mode == "full" or (self.mode == "local" and self.local_model)

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
        if self.values_visible_to_model:
            return error
        return "SQL execution failed. Recheck identifiers, types, grouping and DuckDB syntax."

    @property
    def llm_summary_allowed(self) -> bool:
        return self.values_visible_to_model
