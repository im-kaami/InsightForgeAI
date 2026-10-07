"""Seed the local test databases (see tests/test_database_connectors.py) with a small sales dataset."""

from pathlib import Path

SEED_DIR = Path(__file__).parent / "fixtures" / "db_seed"


def seed_postgres(uri: str) -> None:
    import psycopg

    with psycopg.connect(uri, autocommit=True) as connection:
        connection.execute((SEED_DIR / "postgres.sql").read_text(encoding="utf-8"))


def seed_mssql(uri: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(uri, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            script = (SEED_DIR / "mssql.sql").read_text(encoding="utf-8")
            for statement in [part for part in script.split(";\n") if part.strip()]:
                connection.execute(text(statement))
    finally:
        engine.dispose()
