"""Alembic migration smoke test (Phase 5): applying every migration to an empty
database succeeds, produces the expected schema, and downgrades cleanly back to
empty. Skips when Postgres is unreachable (same posture as the notes tests).

Migrations run against a **throwaway database** this test creates and drops --
never the dev database in ``settings.database_url``. Proving migrations build a
schema from empty requires *starting* from empty, so the test is inherently
destructive and needs a database nobody else owns; pointing it at the dev
database silently wiped real session data on every run. The target is handed to
Alembic through a ``DATABASE_URL`` env override, which pydantic-settings
resolves ahead of the repo ``.env`` -- so ``alembic/env.py`` picks it up with no
config changes.
"""

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from voice_assistant.config import settings

_BACKEND = Path(__file__).resolve().parents[1]

# CREATE/DROP DATABASE cannot be issued from within the database being altered,
# so they go to this maintenance database on the same server.
_ADMIN_DB = "postgres"

_EXPECTED_TABLES = ("notes", "sessions", "events")


def _url_for(database: str) -> str:
    """The configured Postgres URL, repointed at ``database``."""
    url = make_url(settings.database_url).set(database=database)
    return url.render_as_string(hide_password=False)


async def _postgres_reachable() -> bool:
    engine = create_async_engine(_url_for(_ADMIN_DB))
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - any connect failure => skip
        return False
    finally:
        await engine.dispose()


@asynccontextmanager
async def _throwaway_database() -> AsyncIterator[str]:
    """Create a uniquely-named database, yield its URL, and always drop it.

    The name is unique per run so concurrent runs -- and any database orphaned
    by a previously crashed run -- can never collide. ``WITH (FORCE)``
    terminates connections Alembic's subprocess may have left behind, so
    teardown can't be blocked into leaking a database. ``CREATE``/``DROP
    DATABASE`` can't run inside a transaction, hence ``AUTOCOMMIT``.
    """
    name = f"voice_assistant_test_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(_url_for(_ADMIN_DB), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
        try:
            yield _url_for(name)
        finally:
            async with admin.connect() as conn:
                await conn.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
    finally:
        await admin.dispose()


def _run_alembic(*args: str, database_url: str) -> None:
    """Run an Alembic command against ``database_url``, asserting it succeeded."""
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=_BACKEND,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": database_url},
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


async def _existing_tables(database_url: str) -> set[str]:
    """Which of ``_EXPECTED_TABLES`` currently exist in ``database_url``."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            found = set()
            for table in _EXPECTED_TABLES:
                exists = await conn.execute(
                    sa.text(f"SELECT to_regclass('public.{table}')")
                )
                if exists.scalar() == table:
                    found.add(table)
            return found
    finally:
        await engine.dispose()


async def test_alembic_upgrade_and_downgrade_round_trip() -> None:
    if not await _postgres_reachable():
        pytest.skip("Postgres not reachable; skipping migration smoke test")

    async with _throwaway_database() as database_url:
        # A freshly created database is already empty, so this is a true
        # from-empty upgrade -- no destructive downgrade needed to get there.
        _run_alembic("upgrade", "head", database_url=database_url)
        assert await _existing_tables(database_url) == set(_EXPECTED_TABLES)

        # Downgrades are only safe to exercise because the target is
        # disposable -- and worth exercising, since a broken ``downgrade`` is
        # otherwise discovered during an incident rollback.
        _run_alembic("downgrade", "base", database_url=database_url)
        assert await _existing_tables(database_url) == set()
