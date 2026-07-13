"""Alembic upgrade-head smoke test (Phase 5): applying every migration to an
empty database succeeds and produces the expected schema. Skips when Postgres
is unreachable (same posture as the notes tests)."""

import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from voice_assistant.config import settings

_BACKEND = Path(__file__).resolve().parents[1]


async def _postgres_reachable() -> bool:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - any connect failure => skip
        return False
    finally:
        await engine.dispose()


async def test_alembic_upgrade_head_builds_schema() -> None:
    if not await _postgres_reachable():
        pytest.skip("Postgres not reachable; skipping migration smoke test")

    # Fresh slate, then upgrade head, so this is a true from-empty run.
    for cmd in (["alembic", "downgrade", "base"], ["alembic", "upgrade", "head"]):
        result = subprocess.run(
            ["uv", "run", *cmd],
            cwd=_BACKEND,
            capture_output=True,
            text=True,
            env={**os.environ},
        )
        assert result.returncode == 0, f"{cmd} failed:\n{result.stdout}\n{result.stderr}"

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            exists = await conn.execute(sa.text("SELECT to_regclass('public.notes')"))
            assert exists.scalar() == "notes"
    finally:
        await engine.dispose()
