from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Turn(BaseModel):
    goal: str
    summary: str
    table_names: list[str] = Field(default_factory=list)
    key_numbers: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationMemory(BaseModel):
    turns: list[Turn] = Field(default_factory=list)
    max_turns: int = 5

    def add(
        self,
        goal: str,
        summary: str,
        table_names: list[str],
        key_numbers: list[str] | None = None,
        assumptions: list[str] | None = None,
        queries: list[str] | None = None,
    ) -> None:
        self.turns.append(
            Turn(
                goal=goal,
                summary=summary,
                table_names=table_names,
                key_numbers=key_numbers or [],
                assumptions=assumptions or [],
                queries=queries or [],
            )
        )
        self.turns = self.turns[-self.max_turns :]

    def to_prompt(self, include_values: bool = True) -> str:
        blocks = []
        for turn in self.turns:
            lines = [f"Q: {turn.goal}" if include_values else f"Previous user question: {turn.goal}"]
            if turn.queries:
                lines.append("Queries: " + " | ".join(query[:300] for query in turn.queries[:3]))
            if include_values and turn.key_numbers:
                lines.append("Key numbers: " + "; ".join(turn.key_numbers[:6]))
            if turn.assumptions:
                lines.append("Assumptions: " + " ".join(turn.assumptions[:3]))
            if include_values:
                lines.append(f"A (short): {turn.summary[:300]}")
            blocks.append("\n".join(lines))
        return "\n".join(blocks)
