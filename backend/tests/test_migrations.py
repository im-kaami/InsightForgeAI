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
    version_columns = {column["name"] for column in inspect(engine).get_columns("dataset_versions")}
    assert {"recipe_json", "validation_json"} <= version_columns
    assert "saved_models" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "0006_saved_models"
        )
    engine.dispose()
    get_settings.cache_clear()


def test_upgrade_from_0002_preserves_existing_rows(tmp_path, monkeypatch):
    database = tmp_path / "existing.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).parents[1] / "insightforge" / "db" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0002_schedule_timezone")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,created_at) "
                "VALUES ('u','u@example.com','hash','2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id,owner_id,name,kind,sources_json,schema_json,tables_json,created_at,updated_at) "
                "VALUES ('d','u','Data','files','[]','{}','[]','2026-01-01','2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sessions (id,owner_id,dataset_id,title,created_at) "
                "VALUES ('s','u','d','Session','2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runs "
                "(id,session_id,owner_id,goal,status,timings_json,token_usage_json,"
                "used_fallback_plan,created_at) "
                "VALUES ('r','s','u','Goal','completed','{}','{}',0,'2026-01-01')"
            )
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM datasets WHERE id='d'")).scalar() == "Data"
        row = connection.execute(
            text(
                "SELECT verification_status, request_json, provenance_json, warnings_json "
                "FROM runs WHERE id='r'"
            )
        ).one()
        assert tuple(row) == ("exploratory", "{}", "{}", "[]")
        assert connection.execute(text("SELECT notes_json FROM datasets WHERE id='d'")).scalar() == "{}"
        assert tuple(
            connection.execute(text("SELECT recipe_json, rules_json FROM datasets WHERE id='d'")).one()
        ) == ("{}", "{}")
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "0006_saved_models"
        )
    engine.dispose()
    get_settings.cache_clear()
