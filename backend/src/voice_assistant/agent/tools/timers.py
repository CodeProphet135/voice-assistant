"""Timer tools — ``set_timer``, ``list_timers``, ``cancel_timer``.

``set_timer`` returns immediately with a spoken confirmation; the actual
firing (emit ``timer_fired`` + speak a fixed phrase) is owned by
``Session.schedule_timer`` / ``Session._fire_timer`` so the timer task's
lifecycle is tied to the connection (cancelled on teardown). ``list_timers``
and ``cancel_timer`` read/cancel that same per-session registry; timer ids
are session-scoped, so the model finds them via ``list_timers`` first.
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


@tool(
    name="list_timers",
    description=(
        "List the currently running countdown timers, with each timer's id, "
        "label, and remaining time. Use this to find a timer's id before "
        "cancelling it with cancel_timer."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
)
async def list_timers(ctx) -> str:
    timers = ctx.session.list_timers()
    if not timers:
        return "There are no timers running."
    lines = []
    for entry in timers:
        name = f"'{entry['label']}' timer" if entry["label"] else "unlabeled timer"
        remaining = _duration_phrase(entry["remaining_seconds"])
        lines.append(f"{name} (id {entry['timer_id']}): {remaining} remaining")
    return "Running timers:\n" + "\n".join(lines)


@tool(
    name="cancel_timer",
    description=(
        "Cancel a running countdown timer by its id. Call list_timers first "
        "to find the id of the timer to cancel."
    ),
    parameters={
        "type": "object",
        "properties": {
            "timer_id": {
                "type": "string",
                "description": "Id of the timer to cancel, as reported by list_timers.",
            },
        },
        "required": ["timer_id"],
        "additionalProperties": False,
    },
)
async def cancel_timer(ctx, *, timer_id: str) -> str:
    if not ctx.session.cancel_timer(timer_id):
        return "Error: no running timer with that id. Use list_timers to see the active timers."
    return "Timer cancelled."
