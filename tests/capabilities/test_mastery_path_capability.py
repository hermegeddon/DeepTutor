"""Focused tests for the mastery_path capability contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import deeptutor.agents.chat.agent_loop as agent_loop_module
from deeptutor.agents.chat.agent_loop import AgentLoop, AgentLoopState, LLMCallResult
from deeptutor.capabilities.mastery.capability import MasteryPathCapability, resolve_mastery_path_id
from deeptutor.core.context import UnifiedContext


class _FakeUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.estimated_calls: list[dict[str, int]] = []

    def add_from_response(self, _usage: object) -> None:
        self.calls += 1

    def add_estimated(self, *, input_chars: int, output_chars: int) -> None:
        self.calls += 1
        self.estimated_calls.append(
            {"input_chars": input_chars, "output_chars": output_chars}
        )


class _FakePipeline:
    binding = None
    model = "test-model"
    max_rounds = 2
    loop_max_tokens = 256

    def __init__(self) -> None:
        self.usage = _FakeUsage()

    def _t(self, _key: str, *, default: str, **_kwargs: object) -> str:
        return default

    async def _guard_context_window(
        self, _messages: list[dict[str, object]], _stream: object
    ) -> None:
        return None

    def _completion_kwargs(self, *, max_tokens: int) -> dict[str, int]:
        return {"max_tokens": max_tokens}

    def effective_max_rounds(self, _context: UnifiedContext) -> int:
        return self.max_rounds


class _SpyStream:
    def __init__(self) -> None:
        self.progress_events: list[dict[str, object]] = []
        self.content_events: list[dict[str, object]] = []
        self.thinking_events: list[dict[str, object]] = []

    async def progress(
        self,
        content: str,
        *,
        source: str,
        stage: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.progress_events.append(
            {
                "content": content,
                "source": source,
                "stage": stage,
                "metadata": metadata or {},
            }
        )

    async def content(
        self,
        content: str,
        *,
        source: str,
        stage: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.content_events.append(
            {
                "content": content,
                "source": source,
                "stage": stage,
                "metadata": metadata or {},
            }
        )

    async def thinking(
        self,
        content: str,
        *,
        source: str,
        stage: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.thinking_events.append(
            {
                "content": content,
                "source": source,
                "stage": stage,
                "metadata": metadata or {},
            }
        )


def _make_loop(
    *,
    mastery_mode: bool,
    enabled_tools: list[str],
    stream: object | None = None,
) -> AgentLoop:
    return AgentLoop(
        pipeline=_FakePipeline(),
        context=UnifiedContext(metadata={"mastery_mode": mastery_mode}),
        stream=stream or object(),
        client=object(),
        enabled_tools=enabled_tools,
        tool_schemas=[],
    )


def test_mastery_path_capability_manifest_is_registered() -> None:
    assert MasteryPathCapability.manifest.name == "mastery_path"
    assert "mastery_status" in MasteryPathCapability.manifest.tools_used
    assert "mastery_build" in MasteryPathCapability.manifest.tools_used


def test_resolve_mastery_path_id_prefers_metadata_override() -> None:
    context = UnifiedContext(
        session_id="session-id",
        metadata={"mastery_path_id": "acumatica:courses raw"},
    )

    assert resolve_mastery_path_id(context) == "acumatica_courses_raw"


def test_resolve_mastery_path_id_falls_back_to_book_then_session() -> None:
    from_book = UnifiedContext(
        session_id="session-id",
        metadata={"book_references": [{"book_id": "book-id"}]},
    )
    from_session = UnifiedContext(session_id="session-id")

    assert resolve_mastery_path_id(from_book) == "book-id"
    assert resolve_mastery_path_id(from_session) == "session-id"


def test_mastery_loop_nudges_toolless_first_finish() -> None:
    loop = _make_loop(mastery_mode=True, enabled_tools=["mastery_status", "mastery_build"])

    assert loop._should_nudge_mastery_toolless_finish(
        state=AgentLoopState(tool_steps=0), already_nudged=False
    )
    assert "mastery_build" in loop._mastery_toolless_finish_nudge()


def test_mastery_loop_nudge_does_not_affect_chat_or_after_tools() -> None:
    normal_chat = _make_loop(mastery_mode=False, enabled_tools=["mastery_status"])
    after_tool_step = _make_loop(mastery_mode=True, enabled_tools=["mastery_status"])
    already_nudged = _make_loop(mastery_mode=True, enabled_tools=["mastery_status"])
    no_mastery_tools = _make_loop(mastery_mode=True, enabled_tools=["rag"])

    assert not normal_chat._should_nudge_mastery_toolless_finish(
        state=AgentLoopState(tool_steps=0), already_nudged=False
    )
    assert not after_tool_step._should_nudge_mastery_toolless_finish(
        state=AgentLoopState(tool_steps=1), already_nudged=False
    )
    assert not already_nudged._should_nudge_mastery_toolless_finish(
        state=AgentLoopState(tool_steps=0), already_nudged=True
    )
    assert not no_mastery_tools._should_nudge_mastery_toolless_finish(
        state=AgentLoopState(tool_steps=0), already_nudged=False
    )


@pytest.mark.asyncio
async def test_mastery_toolless_nudge_marks_rejected_draft_as_narration() -> None:
    stream = _SpyStream()
    loop = _make_loop(
        mastery_mode=True,
        enabled_tools=["mastery_status", "mastery_build"],
        stream=stream,
    )
    results = [
        LLMCallResult(
            text="draft path that must not be persisted",
            call_id="call-draft",
            call_kind="agent_loop_round",
        ),
        LLMCallResult(
            text="final after nudge",
            call_id="call-final",
            call_kind="agent_loop_round",
        ),
    ]

    async def fake_call_llm(**_kwargs: object) -> LLMCallResult:
        return results.pop(0)

    loop._call_llm = fake_call_llm  # type: ignore[method-assign]

    outcome = await loop._run_loop(
        messages=[], state=AgentLoopState(), checkpoint_boundary=0
    )

    assert outcome.final_text == "final after nudge"
    narration_markers = [
        event
        for event in stream.progress_events
        if event["metadata"].get("call_role") == "narration"
    ]
    assert len(narration_markers) == 1
    metadata = narration_markers[0]["metadata"]
    assert metadata["call_id"] == "call-draft"
    assert metadata["call_role_override"] is True
    assert metadata["override_reason"] == "mastery_toolless_finish_nudge"


class _DelayedResponseStream:
    def __init__(self, *, delay: float, content: str) -> None:
        self.delay = delay
        self.content = content
        self.closed = False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        await asyncio.sleep(self.delay)
        yield SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(content=self.content),
                )
            ],
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_llm_call_emits_heartbeat_during_provider_silence(monkeypatch) -> None:
    monkeypatch.setattr(agent_loop_module, "LLM_CALL_HEARTBEAT_SECONDS", 0.01)
    stream = _SpyStream()
    loop = _make_loop(mastery_mode=False, enabled_tools=[], stream=stream)
    response_stream = _DelayedResponseStream(delay=0.03, content="OK")

    async def fake_create_response_stream(
        _kwargs: dict[str, object], _trace_meta: dict[str, object], _stage: str
    ) -> _DelayedResponseStream:
        await asyncio.sleep(0.03)
        return response_stream

    loop._create_response_stream = fake_create_response_stream  # type: ignore[method-assign]

    result = await loop._call_llm(
        messages=[{"role": "user", "content": "Say OK"}],
        label="Exploring",
        call_kind="agent_loop_round",
        trace_role="explore",
        max_tokens=16,
        tool_schemas=[],
    )

    assert result.text == "OK"
    assert response_stream.closed
    heartbeat_events = [
        event
        for event in stream.progress_events
        if event["metadata"].get("trace_kind") == "call_heartbeat"
    ]
    assert heartbeat_events
    assert heartbeat_events[0]["metadata"].get("call_state") == "running"
