from typing import Any

import pandas as pd

from insightforge.core.llm import LLMClient, LLMResponse
from insightforge.core.memory import ConversationMemory


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pipe_table(df: pd.DataFrame, limit: int) -> str:
    frame = df.head(limit)
    columns = [str(column) for column in frame.columns]
    if not columns:
        return "(no columns)"
    lines = [
        "| " + " | ".join(map(_cell, columns)) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    )
    return "\n".join(lines)


class Summarizer:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def build_context(
        self,
        goal: str,
        tables: dict[str, pd.DataFrame],
        focus: str = "",
        memory: ConversationMemory | None = None,
    ) -> str:
        parts = [f"Goal: {goal}"]
        if focus:
            parts.append(f"Focus: {focus}")
        if memory and memory.turns:
            parts.append(f"Conversation so far:\n{memory.to_prompt()}")
        for name, df in tables.items():
            parts.append(f"Table: {name}\nTotal rows: {len(df)}\n{_pipe_table(df, 20)}")
            numeric = df.select_dtypes(include="number")
            if not numeric.empty:
                stats = numeric.describe().loc[["count", "mean", "min", "max"]].round(2)
                parts.append(f"Numeric summary for {name}:\n{_pipe_table(stats.reset_index(), 20)}")
        return "\n\n".join(parts)

    def summarize(
        self,
        goal: str,
        tables: dict[str, pd.DataFrame],
        focus: str = "",
        memory: ConversationMemory | None = None,
    ) -> tuple[str, LLMResponse | None]:
        context = self.build_context(goal, tables, focus, memory)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a senior business analyst. Write an executive summary in markdown with a "
                    "2-3 sentence headline containing concrete numbers from the data, a 'Key findings' "
                    "bullet list where each bullet has a number, and a 'Recommended actions' bullet list. "
                    "Never invent figures not present in the supplied data."
                ),
            },
            {"role": "user", "content": context},
        ]
        try:
            response = self.llm.chat(messages)
            return response.text, response
        except Exception:
            lines = ["## Analysis summary"]
            for name, df in tables.items():
                lines.append(f"\n### {name}\nRows: {len(df)}\n\n{_pipe_table(df, 5)}")
            if not tables:
                lines.append("\nNo result tables were available.")
            return "\n".join(lines), None
