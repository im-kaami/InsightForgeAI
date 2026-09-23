from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from insightforge.config import get_settings

engine: Engine | None = None
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_current_url = ""


def configure() -> Engine:
    global engine, _current_url
    settings = get_settings()
    if engine is None or _current_url != settings.database_url:
        if engine is not None:
            engine.dispose()
        kwargs = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        engine = create_engine(settings.database_url, connect_args=kwargs)
        SessionLocal.configure(bind=engine)
        _current_url = settings.database_url
    return engine


def get_db() -> Generator[Session, None, None]:
    configure()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    settings = get_settings()
    if not settings.auto_create_tables:
        return
    current_engine = configure()
    tables = set(inspect(current_engine).get_table_names()) - {"alembic_version"}
    with current_engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    if tables and current_revision is None:
        command.stamp(config, "0001_initial")
    command.upgrade(config, "head")
