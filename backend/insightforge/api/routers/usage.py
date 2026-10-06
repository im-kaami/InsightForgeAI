from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.schemas import UsageByDay, UsageByModel, UsageOut, UsageTotals
from insightforge.db.models import Run

router = APIRouter(prefix="/usage", tags=["usage"])


class _Sum:
    def __init__(self) -> None:
        self.runs = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.unpriced_runs = 0

    def add(self, run: Run) -> None:
        usage = run.token_usage_json or {}
        self.runs += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        if run.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += run.cost_usd

    def fields(self) -> dict[str, int | float]:
        return {
            "runs": self.runs,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "unpriced_runs": self.unpriced_runs,
        }


@router.get("", response_model=UsageOut)
def usage(db: Db, user: CurrentUser, days: Annotated[int, Query(ge=1, le=365)] = 30):
    """Totals of the caller's AI-answered runs; costs are estimates from configured prices."""
    since = datetime.now(UTC) - timedelta(days=days)
    runs = db.scalars(
        select(Run)
        .where(Run.owner_id == user.id, Run.created_at >= since, Run.llm_model.is_not(None))
        .order_by(Run.created_at)
    ).all()
    total = _Sum()
    models: dict[tuple[str, str], _Sum] = {}
    daily: dict[str, _Sum] = {}
    for run in runs:
        created = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=UTC)
        total.add(run)
        models.setdefault((run.llm_provider or "", run.llm_model or ""), _Sum()).add(run)
        daily.setdefault(created.astimezone(UTC).strftime("%Y-%m-%d"), _Sum()).add(run)
    return UsageOut(
        days=days,
        since=since,
        totals=UsageTotals(**total.fields()),
        by_model=[
            UsageByModel(provider=provider, model=model, **item.fields())
            for (provider, model), item in sorted(models.items(), key=lambda pair: -pair[1].runs)
        ],
        by_day=[UsageByDay(date=date, **item.fields()) for date, item in sorted(daily.items())],
    )
