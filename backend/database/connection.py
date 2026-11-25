import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_PATH = PROJECT_ROOT / ".env.postgres"


def get_database_url():
    """Build the database URL without hardcoding credentials."""

    configured_url = os.getenv("DATABASE_URL")

    if configured_url:
        return make_url(configured_url)

    config = dotenv_values(ENV_PATH)

    required = [
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ]

    credentials = {key: os.getenv(key) or config.get(key) for key in required}
    if any(not credentials[key] for key in required):
        raise RuntimeError(
            "Missing PostgreSQL configuration in environment or .env.postgres"
        )

    return URL.create(
        drivername="postgresql+psycopg",
        username=credentials["POSTGRES_USER"],
        password=credentials["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        database=credentials["POSTGRES_DB"],
    )


def get_engine():
    """Create a SQLAlchemy database engine."""

    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )
