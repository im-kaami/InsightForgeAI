import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.agent import InsightForgeAgent
from insightforge.core.llm import LLMClient, build_llm
from insightforge.core.memory import ConversationMemory
from insightforge.db.models import Artifact, ChatSession, Connection, Dataset, Run
from insightforge.db.session import SessionLocal, configure
from insightforge.services.crypto import decrypt
from insightforge.services.datasets import open_catalog
from insightforge.services.events import RunEventBus
from insightforge.services.storage import Storage


def start_run(
    db_factory: Callable[[], Session],
    bus: RunEventBus,
    run_id: str,
    tasks: set[asyncio.Task[Any]],
    llm: LLMClient | None = None,
) -> asyncio.Task[Any]:
    task = asyncio.create_task(asyncio.to_thread(execute_run, run_id, db_factory, bus, llm))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


def execute_run(
    run_id: str,
    db_factory: Callable[[], Session] | None = None,
    bus: RunEventBus | None = None,
    llm: LLMClient | None = None,
) -> None:
    configure()
    factory = db_factory or SessionLocal
    db = factory()
    catalog = None

    def publish(event: dict[str, Any]) -> None:
        if bus and event.get("type") != "done":
            bus.publish(run_id, event)

    try:
        run = db.get(Run, run_id)
        if not run:
            return
        run.status = "running"
        db.commit()
        chat = db.get(ChatSession, run.session_id)
        dataset = db.get(Dataset, chat.dataset_id)
        connection = db.get(Connection, dataset.connection_id) if dataset.connection_id else None
        uri = decrypt(connection.encrypted_uri) if connection else None
        catalog = open_catalog(dataset, uri, for_run=True)
        previous = db.scalars(
            select(Run)
            .where(Run.session_id == run.session_id, Run.status == "completed", Run.id != run.id)
            .order_by(Run.created_at.desc())
            .limit(5)
        ).all()
        memory = ConversationMemory()
        for item in reversed(previous):
            memory.add(item.goal, item.summary or "", dataset.tables_json)
        settings = get_settings()
        agent = InsightForgeAgent(
            llm or build_llm(),
            artifact_dir=Storage(settings.storage_dir).run_dir(run.owner_id, run.id),
            render_png=True,
            query_timeout=settings.query_timeout_seconds,
            summary_max_rows=settings.llm_summary_max_rows,
            schema_sample_rows=3 if settings.llm_send_sample_values else 0,
        )
        result = agent.run(run.goal, catalog, memory, on_event=publish)
        run.status = "completed"
        run.plan_json = result.plan.model_dump(mode="json")
        run.summary = result.summary
        run.timings_json = result.timings
        run.token_usage_json = result.token_usage
        run.used_fallback_plan = result.used_fallback_plan
        run.finished_at = datetime.now(UTC)
        for position, artifact in enumerate(result.artifacts):
            payload = artifact.model_dump(mode="json")
            file_path = payload.get("png_path") or payload.get("csv_path")
            db.add(
                Artifact(
                    run_id=run.id,
                    position=position,
                    type=artifact.type,
                    name=artifact.name,
                    payload_json=payload,
                    file_path=file_path,
                )
            )
        db.commit()
        if bus:
            bus.publish(run_id, {"type": "done", "run_id": run.id})
    except Exception as error:
        db.rollback()
        run = db.get(Run, run_id)
        if run:
            run.status = "failed"
            run.error = str(error)
            run.finished_at = datetime.now(UTC)
            db.commit()
        if bus:
            bus.publish(run_id, {"type": "error", "message": str(error)})
    finally:
        if catalog:
            catalog.close()
        if bus:
            bus.finish(run_id)
        db.close()
