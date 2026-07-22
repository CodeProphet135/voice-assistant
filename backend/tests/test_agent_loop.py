"""Async tests for the manual streaming tool-use loop in agent/agent.py.

pytest-asyncio is configured in auto mode (pyproject.toml), so plain `async
def test_*` functions are collected and run without extra markers.
"""

from conftest import FakeOpenAI, make_function_call_turn, make_text_turn

from voice_assistant.agent.agent import MAX_TOOL_LOOP_ITERATIONS, run_agent
from voice_assistant.protocol import (
    AssistantDeltaEvent,
    AssistantDoneEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class Recorder:
    """In-memory emit()/on_sentence() sinks for assertions."""

    def __init__(self) -> None:
        self.events: list = []
        self.sentences: list[str] = []

    async def emit(self, event) -> None:
        self.events.append(event)

    async def on_sentence(self, sentence: str) -> None:
        self.sentences.append(sentence)


async def never_called_tool_executor(name: str, arguments: str, call_id: str) -> str:
    raise AssertionError("tool_executor should not have been called")


async def test_plain_text_turn_streams_deltas_and_emits_done() -> None:
    fake = FakeOpenAI()
    fake.responses.script(make_text_turn("Hello there friend"))
    recorder = Recorder()
    input_items: list[dict] = [{"role": "user", "content": "hi"}]

    full_text = await run_agent(
        client=fake,
        input_items=input_items,
        tools=[],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=never_called_tool_executor,
    )

    assert full_text == "Hello there friend"

    delta_events = [e for e in recorder.events if isinstance(e, AssistantDeltaEvent)]
    assert [e.text for e in delta_events] == ["Hello ", "there ", "friend"]

    done_events = [e for e in recorder.events if isinstance(e, AssistantDoneEvent)]
    assert len(done_events) == 1
    assert done_events[0].text == "Hello there friend"

    # No sentence-final punctuation in "Hello there friend" -> nothing is fed
    # to on_sentence until the trailing flush() at the end of the turn.
    assert recorder.sentences == ["Hello there friend"]

    # The reply must be recorded in the conversation history — otherwise the
    # model sees the question as unanswered next turn and re-answers it.
    assert input_items == [
        {"role": "user", "content": "hi"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello there friend"}],
        },
    ]

    assert len(fake.responses.calls) == 1


async def test_plain_text_turn_with_sentence_boundary_feeds_on_sentence_early() -> None:
    fake = FakeOpenAI()
    fake.responses.script(make_text_turn("Hi there. Bye now"))
    recorder = Recorder()
    input_items: list[dict] = []

    full_text = await run_agent(
        client=fake,
        input_items=input_items,
        tools=[],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=never_called_tool_executor,
    )

    assert full_text == "Hi there. Bye now"
    # "Hi there." completes mid-stream; "Bye now" is flushed at the end.
    assert recorder.sentences == ["Hi there.", "Bye now"]


async def test_function_call_turn_executes_tool_and_continues() -> None:
    fake = FakeOpenAI()
    fake.responses.script(
        make_function_call_turn(call_id="call_1", name="get_weather", arguments='{"city":"Tokyo"}')
    )
    fake.responses.script(make_text_turn("It is sunny in Tokyo"))

    recorder = Recorder()
    input_items: list[dict] = [{"role": "user", "content": "weather in tokyo?"}]

    calls_made: list[tuple[str, str, str]] = []

    async def tool_executor(name: str, arguments: str, call_id: str) -> str:
        calls_made.append((name, arguments, call_id))
        return "sunny, 22C"

    full_text = await run_agent(
        client=fake,
        input_items=input_items,
        tools=[{"type": "function", "name": "get_weather"}],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=tool_executor,
    )

    assert full_text == "It is sunny in Tokyo"
    assert calls_made == [("get_weather", '{"city":"Tokyo"}', "call_1")]
    assert len(fake.responses.calls) == 2

    tool_call_events = [e for e in recorder.events if isinstance(e, ToolCallEvent)]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].call_id == "call_1"
    assert tool_call_events[0].name == "get_weather"

    tool_result_events = [e for e in recorder.events if isinstance(e, ToolResultEvent)]
    assert len(tool_result_events) == 1
    assert tool_result_events[0].call_id == "call_1"
    assert tool_result_events[0].output == "sunny, 22C"

    # input_items: original user message, then the function_call, then its
    # output, then the assistant's final text reply, in that order.
    assert input_items == [
        {"role": "user", "content": "weather in tokyo?"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_weather",
            "arguments": '{"city":"Tokyo"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "sunny, 22C",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "It is sunny in Tokyo"}],
        },
    ]

    done_events = [e for e in recorder.events if isinstance(e, AssistantDoneEvent)]
    assert len(done_events) == 1
    assert done_events[0].text == "It is sunny in Tokyo"


async def test_tool_error_becomes_error_text_output_without_raising() -> None:
    fake = FakeOpenAI()
    fake.responses.script(
        make_function_call_turn(call_id="call_1", name="broken_tool", arguments="{}")
    )
    fake.responses.script(make_text_turn("Something went wrong but I recovered"))

    recorder = Recorder()
    input_items: list[dict] = []

    async def failing_tool_executor(name: str, arguments: str, call_id: str) -> str:
        raise RuntimeError("boom")

    full_text = await run_agent(
        client=fake,
        input_items=input_items,
        tools=[{"type": "function", "name": "broken_tool"}],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=failing_tool_executor,
    )

    assert full_text == "Something went wrong but I recovered"

    output_items = [item for item in input_items if item.get("type") == "function_call_output"]
    assert len(output_items) == 1
    assert output_items[0]["output"] == "Error: boom"

    tool_result_events = [e for e in recorder.events if isinstance(e, ToolResultEvent)]
    assert tool_result_events[0].output == "Error: boom"


async def test_iteration_cap_stops_infinite_function_call_loop() -> None:
    fake = FakeOpenAI()
    # Always return a function call turn, MAX_TOOL_LOOP_ITERATIONS+2 times over
    # (more than the loop should ever consume).
    for i in range(MAX_TOOL_LOOP_ITERATIONS + 2):
        fake.responses.script(
            make_function_call_turn(call_id=f"call_{i}", name="loop_tool", arguments="{}")
        )

    recorder = Recorder()
    input_items: list[dict] = []
    call_count = 0

    async def tool_executor(name: str, arguments: str, call_id: str) -> str:
        nonlocal call_count
        call_count += 1
        return "ok"

    full_text = await run_agent(
        client=fake,
        input_items=input_items,
        tools=[{"type": "function", "name": "loop_tool"}],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=tool_executor,
    )

    # The loop never got a turn without function calls, so there's no
    # trailing assistant text — but it must still terminate cleanly.
    assert full_text == ""
    assert len(fake.responses.calls) == MAX_TOOL_LOOP_ITERATIONS
    assert call_count == MAX_TOOL_LOOP_ITERATIONS

    done_events = [e for e in recorder.events if isinstance(e, AssistantDoneEvent)]
    assert len(done_events) == 1


async def test_cancelled_turn_commits_partial_text_to_history() -> None:
    """Barge-in cancels the agent mid-stream. The text streamed so far was
    (partly) heard by the user, so it must still be committed to the
    conversation history — otherwise the next turn's model sees the question
    as unanswered and answers it all over again (the Loom-demo duplicate)."""
    import asyncio

    from conftest import make_text_delta_event

    release = asyncio.Event()

    class GatedStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield make_text_delta_event("Julius Caesar was ")
            await release.wait()  # never released — the task is cancelled here

    class GatedResponses:
        async def create(self, **kwargs):
            return GatedStream()

    class GatedClient:
        responses = GatedResponses()

    recorder = Recorder()
    input_items: list[dict] = [{"role": "user", "content": "tell me about caesar"}]

    task = asyncio.create_task(
        run_agent(
            client=GatedClient(),
            input_items=input_items,
            tools=[],
            emit=recorder.emit,
            on_sentence=recorder.on_sentence,
            tool_executor=never_called_tool_executor,
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert input_items == [
        {"role": "user", "content": "tell me about caesar"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Julius Caesar was "}],
        },
    ]


async def test_llm_error_commits_partial_text_to_history() -> None:
    """A stream that dies mid-reply already surfaced text to the user; that
    partial text must be committed to history like any other reply."""
    from conftest import make_text_delta_event

    class ExplodingStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield make_text_delta_event("Half an ")
            raise RuntimeError("connection reset")

    class ExplodingResponses:
        async def create(self, **kwargs):
            return ExplodingStream()

    class ExplodingClient:
        responses = ExplodingResponses()

    recorder = Recorder()
    input_items: list[dict] = []

    full_text = await run_agent(
        client=ExplodingClient(),
        input_items=input_items,
        tools=[],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=never_called_tool_executor,
    )

    assert full_text == "Half an "
    assert input_items == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Half an "}],
        },
    ]


async def test_tool_result_event_carries_final_arguments() -> None:
    client = FakeOpenAI()
    client.responses.script(
        make_function_call_turn(call_id="call_1", name="get_weather", arguments='{"city":"Tokyo"}')
    )
    client.responses.script(make_text_turn("done"))
    recorder = Recorder()

    async def tool_executor(name: str, arguments: str, call_id: str) -> str:
        return "sunny"

    await run_agent(
        client=client,
        input_items=[],
        tools=[],
        emit=recorder.emit,
        on_sentence=recorder.on_sentence,
        tool_executor=tool_executor,
    )

    tool_result_events = [e for e in recorder.events if isinstance(e, ToolResultEvent)]
    assert len(tool_result_events) == 1
    assert tool_result_events[0].arguments == '{"city":"Tokyo"}'
    assert tool_result_events[0].output == "sunny"
