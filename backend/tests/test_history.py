"""Pure-function tests for conversation history truncation (Phase 5)."""

from voice_assistant.agent.history import truncate_history


def _user(text: str) -> dict:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _call(call_id: str) -> dict:
    return {"type": "function_call", "call_id": call_id, "name": "get_weather", "arguments": "{}"}


def _output(call_id: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": "ok"}


def test_under_cap_returns_unchanged() -> None:
    items = [_user("a"), _user("b")]
    assert truncate_history(items, max_items=40) == items


def test_over_cap_drops_whole_oldest_turns() -> None:
    # 5 plain user turns; cap 3 -> keep the newest 3, drop the oldest 2.
    items = [_user(str(i)) for i in range(5)]
    result = truncate_history(items, max_items=3)
    assert result == [_user("2"), _user("3"), _user("4")]


def test_never_orphans_a_tool_pair() -> None:
    # Turn layout: user, call, output  (3 items) x 3 turns = 9 items.
    items: list[dict] = []
    for i in range(3):
        items += [_user(str(i)), _call(f"c{i}"), _output(f"c{i}")]
    # Cap 4 would tentatively cut at index 5 (mid-turn 1, between call & output).
    result = truncate_history(items, max_items=4)
    # Must advance to the next user boundary (index 6) -> keep whole turn 2.
    assert result == [_user("2"), _call("c2"), _output("c2")]
    # No orphan: every function_call_output has its function_call present.
    call_ids = {i["call_id"] for i in result if i["type"] == "function_call"}
    for it in result:
        if it["type"] == "function_call_output":
            assert it["call_id"] in call_ids


def test_single_giant_turn_keeps_whole_tail() -> None:
    # One user message followed by 50 call/output items, cap 10: no user
    # boundary at/after the tentative cut -> keep the whole tail intact.
    items: list[dict] = [_user("start")]
    for i in range(25):
        items += [_call(f"c{i}"), _output(f"c{i}")]
    result = truncate_history(items, max_items=10)
    assert result == items  # never orphan; keep everything
