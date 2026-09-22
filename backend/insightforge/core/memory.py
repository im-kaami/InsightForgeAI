from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Turn(BaseModel):
    goal: str
    summary: str
    table_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationMemory(BaseModel):
    turns: list[Turn] = Field(default_factory=list)
    max_turns: int = 5

    def add(self, goal: str, summary: str, table_names: list[str]) -> None:
        self.turns.append(Turn(goal=goal, summary=summary, table_names=table_names))
        self.turns = self.turns[-self.max_turns :]

    def to_prompt(self) -> str:
        return "\n".join(f"Q: {turn.goal}\nA (short): {turn.summary[:300]}" for turn in self.turns)
