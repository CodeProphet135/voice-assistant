"""Tests for the concrete tools (Phase 4). Weather is fully mocked with respx;
notes need Postgres (skip if unreachable); timers use a real Session with fake
WS/TTS and sub-tick durations."""

import os

# Session() constructs a real AsyncOpenAI client (never called here); the SDK
# requires a credential at construction time. Set a harmless placeholder before
# importing anything under voice_assistant.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import asyncio

import httpx
import pytest
import pytest_asyncio
import respx
from conftest import FakeTTSProvider, FakeWebSocket
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from voice_assistant.agent.tools.notes import list_notes, save_note
from voice_assistant.agent.tools.registry import ToolContext
from voice_assistant.agent.tools.timers import set_timer
from voice_assistant.agent.tools.weather import get_weather
from voice_assistant.session import Session


def _timer_session():
    ws = FakeWebSocket()
    session = Session(ws)
    tts = FakeTTSProvider()
    session.tts = tts
    return session, ws, tts


async def _drive(n: int = 50):
    for _ in range(n):
        await asyncio.sleep(0)

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"


@respx.mock
async def test_get_weather_returns_spoken_summary():
    respx.get(_GEOCODE).mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"latitude": 35.68, "longitude": 139.69, "name": "Tokyo"}]},
        )
    )
    respx.get(_FORECAST).mock(
        return_value=httpx.Response(
            200, json={"current": {"temperature_2m": 18.2, "weather_code": 2}}
        )
    )
    out = await get_weather(None, city="Tokyo")
    assert "Tokyo" in out
    assert "18" in out
    assert "partly cloudy" in out


@respx.mock
async def test_get_weather_unknown_city_is_graceful():
    respx.get(_GEOCODE).mock(return_value=httpx.Response(200, json={}))
    out = await get_weather(None, city="Nowhereville")
    assert "couldn't find" in out.lower()
    assert "Nowhereville" in out


@respx.mock
async def test_get_weather_http_error_raises():
    respx.get(_GEOCODE).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await get_weather(None, city="Tokyo")


@pytest_asyncio.fixture
async def notes_db(monkeypatch):
    """Bind the notes tools to a Postgres transaction that is rolled back after
    the test, so nothing persists. Skips if Postgres is unreachable, keeping the
    DB-less suite green everywhere else."""
    from voice_assistant import db as db_module
    from voice_assistant.config import settings
    from voice_assistant.models import Base

    engine = create_async_engine(settings.database_url)
    try:
        conn = await engine.connect()
    except Exception:
        await engine.dispose()
        pytest.skip("Postgres not reachable")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    try:
        yield
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_list_notes_empty(notes_db):
    out = await list_notes(None)
    assert "don't have any notes" in out.lower()


async def test_save_then_list_notes(notes_db):
    assert "saved" in (await save_note(None, text="buy milk")).lower()
    await save_note(None, text="call mom")
    out = await list_notes(None)
    assert "buy milk" in out
    assert "call mom" in out


async def test_set_timer_returns_immediately_and_schedules():
    session, _ws, _tts = _timer_session()
    out = await set_timer(ToolContext(session=session), seconds=300, label="tea")
    assert "5 minutes" in out
    assert len(session._timer_tasks) == 1
    for task in list(session._timer_tasks.values()):
        task.cancel()


async def test_set_timer_rejects_nonpositive():
    session, _ws, _tts = _timer_session()
    with pytest.raises(ValueError):
        await set_timer(ToolContext(session=session), seconds=0, label=None)


async def test_timer_fires_and_speaks():
    session, ws, tts = _timer_session()
    timer_id = session.schedule_timer(0, "tea")
    await _drive()
    assert any(e["type"] == "timer_fired" and e["label"] == "tea" for e in ws.sent)
    assert any("tea" in phrase for phrase in tts.synthesized)
    assert ws.sent_bytes, "the timer should have streamed spoken audio"
    assert timer_id not in session._timer_tasks


async def test_timers_cancelled_on_session_teardown():
    ws = FakeWebSocket()
    session = Session(ws)
    session.tts = FakeTTSProvider()
    session.schedule_timer(1000, "long")
    assert len(session._timer_tasks) == 1
    ws.queue_disconnect()
    await session.run()
    assert session._timer_tasks == {}
