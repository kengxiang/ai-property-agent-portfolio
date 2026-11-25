from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.connection import get_database_url
from backend.database.models import PropertyRecord


@pytest.fixture(scope="module")
def database():
    url = get_database_url()
    name = f"property_test_{uuid4().hex}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    test_url = url.set(database=name)
    engine = create_engine(test_url)
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            command.upgrade(config, "head")
        with Session(engine) as session, session.begin():
            session.add(PropertyRecord(
                property_id="P001", name="KL Central Residence", location_code="KUALA_LUMPUR",
                display_location="Kuala Lumpur", property_type="CONDO", price_myr=550000,
                bedrooms=2, bathrooms=2, availability_status="AVAILABLE", description="Synthetic listing",
            ))
        yield engine
    finally:
        engine.dispose()
        try:
            if created:
                with admin.connect() as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        finally:
            admin.dispose()
