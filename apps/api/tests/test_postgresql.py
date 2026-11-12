"""Opt-in tests: provide a dedicated PostgreSQL database ending in _test."""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def postgres():
    url = os.environ.get("THORAX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("THORAX_TEST_DATABASE_URL not supplied")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        pytest.fail("PostgreSQL test database must end in _test")
    schema = "thorax_test_" + uuid.uuid4().hex
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.commit()
        try:
            target = Path(__file__).parents[1] / "migrations/versions/0001_core.py"
            spec = importlib.util.spec_from_file_location("thorax_test_migration", target)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def test_postgresql_migration_and_append_only_audit(postgres):
    assert len(inspect(postgres).get_table_names()) == 14
    postgres.execute(text("INSERT INTO events (type,data,created_at) VALUES ('synthetic_event','{}',now())"))
    postgres.commit()
    with pytest.raises(DBAPIError):
        postgres.execute(text("UPDATE events SET type='tampered'"))
    postgres.rollback()
    with pytest.raises(DBAPIError):
        postgres.execute(text("DELETE FROM events"))
    postgres.rollback()


def test_postgresql_rejects_non_doctor_assignment(postgres):
    postgres.execute(text("INSERT INTO users (name,email,password_hash,role,is_active,created_at) VALUES ('Synthetic admin','synthetic@example.test','unused','admin',true,now())"))
    postgres.execute(text("INSERT INTO patients (code,name,birth_year,sex,fixture_key,created_at) VALUES ('SYNTHETIC','Synthetic person',1990,'F','test',now())"))
    postgres.commit()
    with pytest.raises(DBAPIError):
        postgres.execute(text("INSERT INTO doctor_patients (doctor_id,patient_id,created_at) SELECT users.id,patients.id,now() FROM users,patients"))
    postgres.rollback()
