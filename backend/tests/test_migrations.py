from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from insightforge.config import get_settings
from insightforge.db.session import configure, init_db


def test_legacy_database_with_empty_alembic_version_is_upgraded(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).parents[1] / "insightforge" / "db" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_initial")

    legacy_engine = create_engine(database_url)
    with legacy_engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
    legacy_engine.dispose()

    init_db()
    engine = configure()
    columns = {column["name"] for column in inspect(engine).get_columns("schedules")}
    assert "timezone" in columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "0002_schedule_timezone"
        )
    engine.dispose()
    get_settings.cache_clear()
