from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from insightforge.config import get_settings
from insightforge.db.models import Base

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
    if settings.auto_create_tables:
        Base.metadata.create_all(configure())
