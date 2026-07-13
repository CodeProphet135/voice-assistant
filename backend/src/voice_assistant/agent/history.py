"""Conversation-history truncation.

The OpenAI conversation state is client-owned (``store=False``), so ``Session``
keeps the running ``input_items`` list itself and re-sends it every turn. Left
unbounded it grows without limit. This trims the oldest *whole turns* once the
list exceeds a cap.

Turns are laid out ``user message -> [function_call..., function_call_output...]``.
A ``user`` message is therefore always a safe cut boundary: cutting there keeps
whole turns and never orphans a ``function_call``/``function_call_output`` pair
(which the Responses API rejects with a 400). The system prompt rides in
``instructions=`` and is never part of ``input_items``, so it is untouched.
"""


def _is_user_message(item: dict) -> bool:
    return item.get("type") == "message" and item.get("role") == "user"


def truncate_history(items: list[dict], max_items: int) -> list[dict]:
    """Return ``items`` trimmed to at most ~``max_items``, dropping only whole
    oldest turns. Returns the same list object when already under the cap.

    The trim point is advanced forward from ``len - max_items`` to the next
    ``user`` message boundary, so the kept tail always starts a clean turn. If
    no user boundary exists at/after that point (one enormous tool-calling
    turn), the whole tail is kept rather than orphan a call/output pair.
    """
    if len(items) <= max_items:
        return items

    tentative = len(items) - max_items
    cut = next(
        (i for i in range(tentative, len(items)) if _is_user_message(items[i])),
        None,
    )
    if cut is None:
        return items
    return items[cut:]
