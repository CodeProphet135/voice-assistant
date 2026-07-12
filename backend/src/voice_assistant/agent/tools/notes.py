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
