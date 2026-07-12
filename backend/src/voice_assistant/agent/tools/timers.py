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
