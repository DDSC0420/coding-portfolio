"""Shared test fixtures. Integration tests need a real PostgreSQL server.

The suite creates two throwaway databases (relay_test_engine, relay_test_crm)
on the server named by RELAY_TEST_ADMIN_URL and migrates them with Alembic.
The default points at the Compose engine database on localhost:55432:

    docker compose up -d engine-db
    uv run pytest

SQLite is deliberately not supported: the claim relies on PostgreSQL row
locking (FOR UPDATE SKIP LOCKED).
"""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text

from relay.db.session import make_engine
from relay.workflows.registry import Registry, default_registry

ADMIN_URL = os.environ.get(
    "RELAY_TEST_ADMIN_URL", "postgresql+psycopg://relay:relay@127.0.0.1:55432/postgres"
)
ENGINE_DB, CRM_DB = "relay_test_engine", "relay_test_crm"
ENGINE_TABLES = "workflow_events, approvals, step_attempts, step_runs, workflow_runs, workers"
CRM_TABLES = "mutations, idempotency_keys, contacts"


def _db_url(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def database_urls() -> dict[str, str]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            for db in (ENGINE_DB, CRM_DB):
                conn.execute(text(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
                conn.execute(text(f'CREATE DATABASE "{db}"'))
    except Exception as exc:
        pytest.exit(
            f"Cannot reach PostgreSQL at {make_url(ADMIN_URL).render_as_string()}: {exc}\n"
            "Start it with `docker compose up -d engine-db` or set RELAY_TEST_ADMIN_URL.",
            returncode=3,
        )
    finally:
        admin.dispose()

    urls = {"engine": _db_url(ENGINE_DB), "crm": _db_url(CRM_DB)}
    for section, url in urls.items():
        cfg = Config("alembic.ini", ini_section=section)
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
    return urls


@pytest.fixture(scope="session")
def _engine_db(database_urls: dict[str, str]) -> Iterator[Engine]:
    engine = make_engine(database_urls["engine"], pool_size=20, application_name="relay-tests")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def _crm_db(database_urls: dict[str, str]) -> Iterator[Engine]:
    engine = make_engine(database_urls["crm"], pool_size=20, application_name="relay-tests-crm")
    yield engine
    engine.dispose()


@pytest.fixture
def engine(_engine_db: Engine) -> Engine:
    """Engine database, emptied before each test."""
    with _engine_db.begin() as conn:
        conn.execute(text(f"TRUNCATE {ENGINE_TABLES} RESTART IDENTITY CASCADE"))
    return _engine_db


@pytest.fixture
def crm_engine(_crm_db: Engine) -> Engine:
    """CRM database, emptied before each test."""
    with _crm_db.begin() as conn:
        conn.execute(text(f"TRUNCATE {CRM_TABLES} RESTART IDENTITY CASCADE"))
    return _crm_db


@pytest.fixture
def registry() -> Registry:
    return default_registry()


VALID_ENQUIRY = {
    "enquiry_ref": "TEST-001",
    "name": "Priya Shah",
    "email": "priya.shah@northwind-logistics.example",
    "company": "Northwind Logistics Ltd",
    "message": "We need to automate stock reconciliation. Budget about £25,000.",
    "source": "web_form",
    "approval_required": False,
}
