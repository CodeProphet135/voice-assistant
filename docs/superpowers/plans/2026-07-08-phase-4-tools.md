# Phase 4 — Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent four working tools — `get_weather`, `set_timer`, `save_note`, `list_notes` — via a `@tool` registry wired into `session.py`'s existing `tool_executor` stub.

**Architecture:** A module-level registry (`agent/tools/registry.py`) collects `@tool`-decorated async handlers. `session.py` calls `registry.definitions()` for the OpenAI tool schemas and `registry.execute()` from its `tool_executor`. The agent loop, protocol events, and frontend tool-chip UI already handle tools generically (built Phases 1–3) — no changes there. Timers schedule a tracked `asyncio.Task` on the session that emits `timer_fired` and speaks a fixed phrase through the existing TTS path. Notes persist to the existing Postgres `notes` table via `db.async_session_factory`.

**Tech Stack:** Python 3.12, FastAPI, OpenAI Responses API, httpx (Open-Meteo, keyless), SQLAlchemy 2.0 async + asyncpg, pytest + pytest-asyncio + respx; frontend Vite/React/TS.

## Global Constraints

- Backend line-length 100; ruff lint set `E, F, I, UP, B`; target `py312`.
- `pytest` runs with `asyncio_mode = "auto"` — async test functions need **no** `@pytest.mark.asyncio`; async fixtures use `@pytest_asyncio.fixture`.
- Existing test suite must stay green **with no OpenAI/Deepgram key and no database** — new DB-dependent tests must `pytest.skip` when Postgres is unreachable. Tests that construct `Session` set `os.environ.setdefault("OPENAI_API_KEY", "test-key")` at module import (the SDK requires a credential at construction).
- OpenAI tool schemas use the Responses flattened shape `{"type": "function", "name", "description", "parameters", "strict": True}`, name-sorted for prompt-cache-prefix stability; strict mode requires **every** property listed in `required` and `additionalProperties: false` (optional args typed as a nullable union, e.g. `["string", "null"]`).
- `web_search` is **out of scope** (deferred).
- Conventional commits, one focused commit per task. Work directly on `main`.
- All backend commands run from `backend/` via `uv run`.

---

## File Structure

**Create:**
- `backend/src/voice_assistant/agent/tools/registry.py` — `@tool` decorator, `ToolSpec`, `ToolContext`, `definitions()`, `execute()`.
- `backend/src/voice_assistant/agent/tools/weather.py` — `get_weather`.
- `backend/src/voice_assistant/agent/tools/notes.py` — `save_note`, `list_notes`.
- `backend/src/voice_assistant/agent/tools/timers.py` — `set_timer`.
- `backend/tests/test_registry.py`
- `backend/tests/test_tools.py`

**Modify:**
- `backend/src/voice_assistant/agent/tools/__init__.py` — import tool modules for `@tool` registration (built up per task).
- `backend/src/voice_assistant/session.py` — wire `_TOOLS`/`tool_executor`; add timer scheduling.
- `frontend/src/state.ts` — `notifications` + `timer_fired` reducer case.
- `frontend/src/App.tsx` — render notifications banner.
- `frontend/src/index.css` — notification styles.

---

## Task 1: Tool registry + session wiring

Builds the registry mechanism and connects it to the session's existing `tool_executor` stub. No concrete tools yet, so `_TOOLS` is empty and every existing test stays green — this task only proves the plumbing.

**Files:**
- Create: `backend/src/voice_assistant/agent/tools/registry.py`
- Modify: `backend/src/voice_assistant/agent/tools/__init__.py` (currently empty)
- Modify: `backend/src/voice_assistant/session.py:49-50` (the `_TOOLS = []` line) and `session.py:102-106` (the `tool_executor` stub)
- Test: `backend/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `registry.tool(*, name: str, description: str, parameters: dict) -> Callable` — decorator returning the handler unchanged after registering it.
  - `registry.definitions() -> list[dict]` — name-sorted flattened tool defs.
  - `registry.execute(name: str, arguments_json: str, ctx: ToolContext) -> str` — dispatch; unknown name / bad JSON return `"Error: ..."` strings; handler exceptions propagate.
  - `registry.ToolContext` — dataclass with field `session`.
  - `registry._REGISTRY: dict[str, ToolSpec]` — module global (used by tests for save/restore isolation).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_registry.py`:

```python
"""Tests for the @tool registry mechanism (Phase 4).

Registration mutates a module-global dict, so an autouse fixture snapshots and
restores it around every test to keep this file hermetic even after the real
tool modules register themselves at import time.
"""

import json

import pytest

from voice_assistant.agent.tools import registry


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def _register_sample_tools():
    @registry.tool(
        name="zzz_sample",
        description="Z sample.",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        },
    )
    async def zzz_sample(ctx, x: str) -> str:
        return f"z:{x}"

    @registry.tool(
        name="aaa_sample",
        description="A sample.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    async def aaa_sample(ctx) -> str:
        return "a"


def test_definitions_are_name_sorted_and_strict_shaped():
    _register_sample_tools()
    defs = registry.definitions()
    sample = [d for d in defs if d["name"] in {"aaa_sample", "zzz_sample"}]
    assert [d["name"] for d in sample] == ["aaa_sample", "zzz_sample"]
    z = next(d for d in defs if d["name"] == "zzz_sample")
    assert z["type"] == "function"
    assert z["strict"] is True
    assert z["parameters"]["additionalProperties"] is False
    assert z["description"] == "Z sample."


async def test_execute_dispatches_to_handler():
    _register_sample_tools()
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("zzz_sample", json.dumps({"x": "hi"}), ctx)
    assert out == "z:hi"


async def test_execute_unknown_tool_returns_error_string():
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("nope", "{}", ctx)
    assert out.startswith("Error:")
    assert "nope" in out


async def test_execute_bad_json_returns_error_string():
    _register_sample_tools()
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("zzz_sample", "{not json", ctx)
    assert out.startswith("Error:")


async def test_execute_propagates_handler_exception():
    @registry.tool(
        name="boom",
        description="Always fails.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )
    async def boom(ctx) -> str:
        raise RuntimeError("kaboom")

    ctx = registry.ToolContext(session=None)
    with pytest.raises(RuntimeError, match="kaboom"):
        await registry.execute("boom", "{}", ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_registry.py -q`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (no `registry` module yet).

- [ ] **Step 3: Write the registry**

Create `backend/src/voice_assistant/agent/tools/registry.py`:

```python
"""The tool registry — a ``@tool`` decorator collecting async handlers plus
the two functions ``session.py`` needs: ``definitions()`` (OpenAI tool schemas)
and ``execute()`` (dispatch).

Handlers are ``async def handler(ctx: ToolContext, **kwargs) -> str``. Handler
exceptions are intentionally NOT caught here — ``agent/agent.py``'s
``_execute_tool_call`` is the single place that maps a tool failure to an
``"Error: ..."`` string the model can read, so the two never double-wrap. The
registry only produces friendly strings for its own dispatch failures (unknown
tool, malformed argument JSON).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_assistant.session import Session


@dataclass
class ToolContext:
    """What a tool handler is handed at call time. ``session`` is the live
    ``Session`` (used by ``set_timer`` to schedule/emit); tools that don't need
    it simply ignore it."""

    session: "Session | None"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[str]]


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *, name: str, description: str, parameters: dict
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Register an async tool handler and return it unchanged."""

    def decorator(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        _REGISTRY[name] = ToolSpec(
            name=name, description=description, parameters=parameters, handler=fn
        )
        return fn

    return decorator


def definitions() -> list[dict]:
    """Return name-sorted OpenAI Responses tool definitions (strict mode)."""
    return [
        {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "strict": True,
        }
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.name)
    ]


async def execute(name: str, arguments_json: str, ctx: ToolContext) -> str:
    """Dispatch a tool call. Dispatch-level failures (unknown tool, bad JSON)
    return an ``"Error: ..."`` string; the handler's own exceptions propagate."""
    spec = _REGISTRY.get(name)
    if spec is None:
        return f"Error: no such tool {name!r}"
    try:
        kwargs: dict[str, Any] = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError:
        return "Error: invalid tool arguments"
    return await spec.handler(ctx, **kwargs)
```

Leave `backend/src/voice_assistant/agent/tools/__init__.py` with just a docstring for now (no tool imports yet):

```python
"""Tool implementations, each registering itself with ``registry`` on import.

``session.py`` imports this package (which imports every tool module below) so
``registry.definitions()`` is fully populated before it is read."""
```

- [ ] **Step 4: Run registry test to verify it passes**

Run: `cd backend && uv run pytest tests/test_registry.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Wire the session**

In `backend/src/voice_assistant/session.py`, replace the tools import/stub.

Change the imports region — add after the existing `from voice_assistant.agent.agent import run_agent` line (session.py:20):

```python
from voice_assistant.agent.tools import registry
from voice_assistant.agent.tools.registry import ToolContext
```

Replace session.py:49-50:

```python
# No tools yet (Phase 4 introduces a registry here).
_TOOLS: list[dict] = []
```

with:

```python
# Tool schemas come from the registry; importing ``registry`` runs the tools
# package __init__, which imports each tool module so it self-registers before
# ``definitions()`` is read here.
_TOOLS: list[dict] = registry.definitions()
```

Replace the `tool_executor` stub (session.py:102-106):

```python
    async def tool_executor(self, name: str, arguments: str, call_id: str) -> str:
        """Tool dispatch stub. The tool list is empty in Phase 1, so this
        should never actually be invoked — Phase 4 replaces it with a real
        registry lookup."""
        raise NotImplementedError(f"No tools are registered yet (attempted: {name!r})")
```

with:

```python
    async def tool_executor(self, name: str, arguments: str, call_id: str) -> str:
        """Dispatch a tool call through the registry, under a ``tool.execute``
        span that nests inside the current ``turn`` span (same pattern as
        ``llm.request``/``tts.synthesize``). ``call_id`` is unused by the
        registry but kept in the signature ``run_agent`` calls with."""
        with _tracer.start_as_current_span("tool.execute") as span:
            span.set_attribute("tool.name", name)
            return await registry.execute(name, arguments, ToolContext(session=self))
```

- [ ] **Step 6: Verify the full backend suite is still green**

Run: `cd backend && uv run pytest -q && uv run ruff check .`
Expected: all existing tests pass (unchanged count — `_TOOLS` is still empty so behavior is identical), ruff clean.

- [ ] **Step 7: Commit**

```bash
git add backend/src/voice_assistant/agent/tools/registry.py \
        backend/src/voice_assistant/agent/tools/__init__.py \
        backend/src/voice_assistant/session.py \
        backend/tests/test_registry.py
git commit -m "feat(backend): tool registry + wire session tool_executor"
```

---

## Task 2: get_weather tool

**Files:**
- Create: `backend/src/voice_assistant/agent/tools/weather.py`
- Modify: `backend/src/voice_assistant/agent/tools/__init__.py`
- Test: `backend/tests/test_tools.py` (new file — weather tests only in this task)

**Interfaces:**
- Consumes: `registry.tool` (Task 1).
- Produces: `weather.get_weather(ctx, *, city: str) -> str` (registered as `get_weather`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tools.py`:

```python
"""Tests for the concrete tools (Phase 4). Weather is fully mocked with respx;
notes need Postgres (skip if unreachable); timers use a real Session with fake
WS/TTS and sub-tick durations."""

import httpx
import pytest
import respx

from voice_assistant.agent.tools.weather import get_weather

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: voice_assistant.agent.tools.weather`.

- [ ] **Step 3: Write the weather tool**

Create `backend/src/voice_assistant/agent/tools/weather.py`:

```python
"""``get_weather`` — Open-Meteo geocoding + current forecast (keyless, no auth).

Two GETs (a fresh ``httpx.AsyncClient`` per call so nothing is left open across
requests): geocode the city to lat/lon, then fetch the current temperature and
WMO weather code. Returns a short spoken-style summary the model relays. A
missing geocoding match returns a normal string (the model should relay it
conversationally); an HTTP failure raises so ``agent/agent.py`` maps it to an
``Error: ...`` output like any other tool failure.
"""

import httpx

from voice_assistant.agent.tools.registry import tool

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes collapsed to ~spoken buckets (not all 27 need distinct copy).
_WMO: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "drizzly",
    53: "drizzly",
    55: "drizzly",
    61: "rainy",
    63: "rainy",
    65: "raining heavily",
    66: "sleeting",
    67: "sleeting",
    71: "snowy",
    73: "snowy",
    75: "snowing heavily",
    77: "snowy",
    80: "showery",
    81: "showery",
    82: "stormy with heavy showers",
    85: "snow-showery",
    86: "snow-showery",
    95: "thundery",
    96: "thundery with hail",
    99: "thundery with hail",
}


@tool(
    name="get_weather",
    description="Get the current weather for a city by name.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Tokyo'."}
        },
        "required": ["city"],
        "additionalProperties": False,
    },
)
async def get_weather(ctx, *, city: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        geo = await client.get(_GEOCODE_URL, params={"name": city, "count": 1})
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"I couldn't find a city called {city}."

        place = results[0]
        forecast = await client.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
            },
        )
        forecast.raise_for_status()
        current = forecast.json()["current"]

    temp = round(current["temperature_2m"])
    desc = _WMO.get(current["weather_code"], "hard to describe")
    return f"It's currently {temp} degrees Celsius and {desc} in {place['name']}."
```

Update `backend/src/voice_assistant/agent/tools/__init__.py` — add the import so it registers:

```python
"""Tool implementations, each registering itself with ``registry`` on import.

``session.py`` imports this package (which imports every tool module below) so
``registry.definitions()`` is fully populated before it is read."""

from voice_assistant.agent.tools import weather  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify registration + suite + lint**

Run:
```bash
cd backend && uv run python -c "from voice_assistant.session import _TOOLS; print([t['name'] for t in _TOOLS])"
```
Expected: `['get_weather']`.

Run: `cd backend && uv run pytest -q && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/voice_assistant/agent/tools/weather.py \
        backend/src/voice_assistant/agent/tools/__init__.py \
        backend/tests/test_tools.py
git commit -m "feat(backend): get_weather tool (Open-Meteo)"
```

---

## Task 3: save_note / list_notes tools

**Files:**
- Create: `backend/src/voice_assistant/agent/tools/notes.py`
- Modify: `backend/src/voice_assistant/agent/tools/__init__.py`
- Test: `backend/tests/test_tools.py` (append notes tests + DB fixture)

**Interfaces:**
- Consumes: `registry.tool` (Task 1); `voice_assistant.db.async_session_factory`; `voice_assistant.models.Note`.
- Produces: `notes.save_note(ctx, *, text: str) -> str` (registered `save_note`); `notes.list_notes(ctx) -> str` (registered `list_notes`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tools.py` (add imports at the top of the file alongside the existing ones):

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from voice_assistant.agent.tools.notes import list_notes, save_note
```

Then append the fixture + tests:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tools.py -q -k notes`
Expected: FAIL — `ModuleNotFoundError: voice_assistant.agent.tools.notes` (import error), OR skip if no Postgres — if it skips, still create the module so import succeeds; the fail-first signal here is the import error, which occurs regardless of DB.

- [ ] **Step 3: Write the notes tools**

Create `backend/src/voice_assistant/agent/tools/notes.py`:

```python
"""``save_note`` / ``list_notes`` — persistence against the existing Postgres
``notes`` table via the shared ``db.async_session_factory``.

The module imports ``db`` (not ``async_session_factory`` by name) so tests can
monkeypatch ``db.async_session_factory`` onto a transaction-bound factory.
"""

from sqlalchemy import select

from voice_assistant import db
from voice_assistant.agent.tools.registry import tool
from voice_assistant.models import Note

_MAX_LISTED = 10


@tool(
    name="save_note",
    description="Save a short note for the user to recall later.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The note text to save."}
        },
        "required": ["text"],
        "additionalProperties": False,
    },
)
async def save_note(ctx, *, text: str) -> str:
    async with db.async_session_factory() as session:
        session.add(Note(text=text))
        await session.commit()
    return "Saved your note."


@tool(
    name="list_notes",
    description="List the notes the user has saved.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
)
async def list_notes(ctx) -> str:
    async with db.async_session_factory() as session:
        result = await session.execute(
            select(Note).order_by(Note.created_at).limit(_MAX_LISTED)
        )
        texts = [n.text for n in result.scalars().all()]

    if not texts:
        return "You don't have any notes yet."
    if len(texts) == 1:
        return f"You have one note: {texts[0]}."
    joined = ", ".join(texts[:-1]) + f", and {texts[-1]}"
    return f"You have {len(texts)} notes: {joined}."
```

Update `backend/src/voice_assistant/agent/tools/__init__.py`:

```python
from voice_assistant.agent.tools import notes, weather  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_tools.py -q -k notes`
Expected: PASS (2 tests) if Postgres is up (start it with `make db-up && make migrate` from repo root if needed), or SKIP if not reachable — either is acceptable; run once with Postgres up to confirm the logic.

- [ ] **Step 5: Verify registration + full suite + lint**

Run: `cd backend && uv run python -c "from voice_assistant.session import _TOOLS; print(sorted(t['name'] for t in _TOOLS))"`
Expected: `['get_weather', 'list_notes', 'save_note']`.

Run: `cd backend && uv run pytest -q && uv run ruff check .`
Expected: all pass/skip, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/voice_assistant/agent/tools/notes.py \
        backend/src/voice_assistant/agent/tools/__init__.py \
        backend/tests/test_tools.py
git commit -m "feat(backend): save_note/list_notes tools (Postgres)"
```

---

## Task 4: set_timer tool + spoken notification + frontend

Timers schedule a tracked task on the session; on fire it emits `timer_fired` and speaks a fixed phrase through the existing TTS path. The frontend surfaces `timer_fired` as a notification (currently swallowed by the reducer default case).

**Files:**
- Create: `backend/src/voice_assistant/agent/tools/timers.py`
- Modify: `backend/src/voice_assistant/agent/tools/__init__.py`
- Modify: `backend/src/voice_assistant/session.py` (import `TimerFiredEvent`; add `_timer_tasks`, `schedule_timer`, `_fire_timer`; cancel timers in `run()` teardown)
- Modify: `frontend/src/state.ts`, `frontend/src/App.tsx`, `frontend/src/index.css`
- Test: `backend/tests/test_tools.py` (append timer tests)

**Interfaces:**
- Consumes: `registry.tool`, `ToolContext.session`; `Session.schedule_timer`, `Session.tts`, `Session._turn_lock`; protocol `TimerFiredEvent`, `StateEvent`, `TtsStartEvent`, `TtsEndEvent`.
- Produces: `timers.set_timer(ctx, *, seconds: int, label: str | None = None) -> str` (registered `set_timer`); `Session.schedule_timer(seconds: int, label: str | None) -> str` returning a `timer_id` hex string.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tools.py`. Add at the very top of the file, **before** any `voice_assistant` import (Session construction needs a key):

```python
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
```

Add these imports alongside the others:

```python
import asyncio

from conftest import FakeTTSProvider, FakeWebSocket
from voice_assistant.agent.tools.registry import ToolContext
from voice_assistant.agent.tools.timers import set_timer
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
```

Then append the tests:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_tools.py -q -k timer`
Expected: FAIL — `ModuleNotFoundError: voice_assistant.agent.tools.timers` (and `Session` has no `schedule_timer`/`_timer_tasks`).

- [ ] **Step 3: Write the timer tool**

Create `backend/src/voice_assistant/agent/tools/timers.py`:

```python
"""``set_timer`` — schedule a background asyncio timer on the session.

The tool returns immediately with a spoken confirmation; the actual firing
(emit ``timer_fired`` + speak a fixed phrase) is owned by
``Session.schedule_timer`` / ``Session._fire_timer`` so the timer task's
lifecycle is tied to the connection (cancelled on teardown).
"""

from voice_assistant.agent.tools.registry import tool


def _duration_phrase(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    parts: list[str] = []
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not minutes:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


@tool(
    name="set_timer",
    description="Set a countdown timer that notifies the user when it elapses.",
    parameters={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "integer",
                "description": "Timer duration in seconds.",
            },
            "label": {
                "type": ["string", "null"],
                "description": "Optional short label, e.g. 'tea'.",
            },
        },
        "required": ["seconds", "label"],
        "additionalProperties": False,
    },
)
async def set_timer(ctx, *, seconds: int, label: str | None = None) -> str:
    if seconds <= 0:
        raise ValueError("Timer duration must be a positive number of seconds.")
    ctx.session.schedule_timer(seconds, label)
    tail = f" for {label}" if label else ""
    return f"Timer set{tail} for {_duration_phrase(seconds)}."
```

Update `backend/src/voice_assistant/agent/tools/__init__.py`:

```python
from voice_assistant.agent.tools import notes, timers, weather  # noqa: F401
```

- [ ] **Step 4: Add the session timer machinery**

In `backend/src/voice_assistant/session.py`, add `TimerFiredEvent` to the protocol import block (alongside `TtsStartEvent` etc., session.py:22-35):

```python
    TimerFiredEvent,
```

In `Session.__init__`, after the `self._turn_task: asyncio.Task | None = None` line (session.py:85), add:

```python
        # Background countdown timers (set_timer tool), keyed by timer_id, so
        # teardown can cancel any still pending and nothing leaks on disconnect.
        self._timer_tasks: dict[str, asyncio.Task] = {}
```

Add these two methods to `Session` (place them just after `_barge_in`, session.py:222):

```python
    def schedule_timer(self, seconds: int, label: str | None) -> str:
        """Start a tracked countdown task and return its id. Called by the
        ``set_timer`` tool; the task fires ``_fire_timer`` when it elapses."""
        timer_id = uuid.uuid4().hex
        self._timer_tasks[timer_id] = asyncio.create_task(
            self._fire_timer(timer_id, seconds, label)
        )
        return timer_id

    async def _fire_timer(self, timer_id: str, seconds: int, label: str | None) -> None:
        """Wait out the timer, emit ``timer_fired``, then speak a fixed phrase
        through the existing TTS path. The spoken part takes ``_turn_lock`` so
        it never talks over an in-flight assistant turn (it just waits its
        turn — a background notification needs no barge-in semantics)."""
        try:
            await asyncio.sleep(seconds)
            await self.emit(TimerFiredEvent(timer_id=timer_id, label=label or ""))
            phrase = f"Your {label} timer is done." if label else "Your timer is done."
            async with self._turn_lock:
                with _tracer.start_as_current_span("turn"):
                    await self.emit(StateEvent(state="speaking"))
                    await self.emit(TtsStartEvent(sentence_index=0, text=phrase))
                    with _tracer.start_as_current_span("tts.synthesize") as span:
                        span.set_attribute("sentence_index", 0)
                        async for chunk in self.tts.synthesize(phrase):
                            await self.ws.send_bytes(chunk)
                    await self.emit(TtsEndEvent())
                    await self.emit(
                        StateEvent(state="listening" if self.stt is not None else "idle")
                    )
        except asyncio.CancelledError:
            raise
        finally:
            self._timer_tasks.pop(timer_id, None)
```

In `Session.run()`'s `finally` block (session.py:346-352), cancel pending timers. Add before the `await self._stop_stt()` line:

```python
            for task in list(self._timer_tasks.values()):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._timer_tasks.clear()
```

(`contextlib` is already imported in session.py.)

- [ ] **Step 5: Run the timer tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_tools.py -q -k timer`
Expected: PASS (4 tests).

- [ ] **Step 6: Full backend suite + lint**

Run: `cd backend && uv run pytest -q && uv run ruff check .`
Expected: all pass/skip, ruff clean. Confirm `_TOOLS` now lists four tools:
`cd backend && uv run python -c "from voice_assistant.session import _TOOLS; print(sorted(t['name'] for t in _TOOLS))"`
Expected: `['get_weather', 'list_notes', 'save_note', 'set_timer']`.

- [ ] **Step 7: Frontend — surface timer_fired**

In `frontend/src/state.ts`:

Add `notifications` to `AppState` (after `error: string | null` in the interface, state.ts:33):

```typescript
  notifications: string[]
```

Add it to `initialState` (after `error: null`, state.ts:43):

```typescript
  notifications: [],
```

Add a reducer case (before the `default:` case, state.ts:155):

```typescript
    case 'timer_fired': {
      const label = action.label ? `${action.label} timer` : 'Timer'
      return { ...state, notifications: [...state.notifications, `${label} done`] }
    }
```

In `frontend/src/App.tsx`, render the banner. After the `{state.error && ...}` line (App.tsx:59), add:

```tsx
      {state.notifications.length > 0 && (
        <ul className="notifications">
          {state.notifications.map((note, index) => (
            <li key={index} className="notification">
              ⏱ {note}
            </li>
          ))}
        </ul>
      )}
```

In `frontend/src/index.css`, append:

```css
.notifications {
  list-style: none;
  margin: 0 0 0.75rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.notification {
  align-self: flex-start;
  padding: 0.375rem 0.75rem;
  border-radius: 999px;
  background: #fef3c7;
  color: #92400e;
  font-size: 0.875rem;
}
```

- [ ] **Step 8: Frontend verification**

Run: `cd frontend && npm run typecheck && npm run build && npx oxlint`
Expected: all clean; the `pcm-worklet` chunk still emits in the build output.

- [ ] **Step 9: Commit**

```bash
git add backend/src/voice_assistant/agent/tools/timers.py \
        backend/src/voice_assistant/agent/tools/__init__.py \
        backend/src/voice_assistant/session.py \
        backend/tests/test_tools.py \
        frontend/src/state.ts frontend/src/App.tsx frontend/src/index.css
git commit -m "feat: set_timer tool + spoken timer notification + UI"
```

---

## Final verification (after all tasks)

- [ ] **Full suites green:** `cd backend && uv run pytest -q && uv run ruff check .`; `cd frontend && npm run typecheck && npm run build && npx oxlint`.
- [ ] **Notes tests exercised against real Postgres at least once** (`make db-up && make migrate`, then `cd backend && uv run pytest tests/test_tools.py -k notes -q` → PASS, not skip).
- [ ] **Live smoke test (needs OpenAI + Deepgram keys, live backend):** `make dev-backend`, then `cd backend && uv run python -u ../scripts/ws_client.py --text "what's the weather in Tokyo?"`. Expect a real `tool_call` (get_weather) → `tool_result` → spoken-style `assistant_delta…assistant_done`, no 400s. This confirms the strict tool schema is accepted by the live Responses API.
- [ ] **Manual browser check** (`make dev-backend` + `make dev-frontend`): ask for weather (tool chip appears and resolves), set a short timer like "set a 5 second timer" (chip resolves, `⏱ Timer done` banner appears and the phrase is spoken), save and then list a note. No console errors.
- [ ] **Update HANDOFF.md** — mark Phase 4 status, note web_search deferral, and the live/manual check results.

## Self-review notes

- Spec coverage: registry (Task 1), get_weather (Task 2), notes (Task 3), set_timer + spoken fire + `timer_fired` UI (Task 4), `tool.execute` span (Task 1 step 5), strict-mode schemas (all tool tasks), Postgres-or-skip tests (Task 3), web_search deferred (not implemented — matches spec). All covered.
- Type consistency: `registry.tool` keyword-only signature is used identically in every tool module; `ToolContext(session=...)` field name matches its dataclass; `schedule_timer(seconds, label)` return type (`str` id) matches its use; `TimerFiredEvent(timer_id=..., label=...)` matches protocol.py:147-152 fields.
- Isolation: `test_registry.py`'s autouse `clean_registry` fixture prevents its sample-tool registrations from leaking into `test_tools.py`'s `_TOOLS` assertions.
