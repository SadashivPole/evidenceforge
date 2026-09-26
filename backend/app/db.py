"""Database engine and dependency readiness helpers."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine for the configured database."""

    return create_engine(settings.database_url, pool_pre_ping=True)


def is_database_ready(engine: Engine) -> bool:
    """Return whether a lightweight database connectivity check succeeds."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True
