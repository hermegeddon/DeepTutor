"""Mastery Path capability — mastery-based tutoring driven by the chat loop.

There is no bespoke state machine here anymore. The chat agent loop IS the
tutor: this capability only marks the turn as mastery mode and resolves the
active path id, then runs the standard agentic chat pipeline. The pipeline
mounts the mastery tools (``mastery_status`` / ``mastery_quiz`` /
``mastery_grade`` / ``mastery_assess`` / ``mastery_build``) and injects the
tutor playbook; the pure engine in :mod:`deeptutor.learning` owns the hard,
per-type mastery gate and the spaced-repetition arithmetic.

Design axiom (shared with chat): the intelligence lives at the loop's exit —
the model decides what to teach and how to question — while the gate that
decides *whether the learner may advance* is a deterministic engine call.
"""

from __future__ import annotations

import re

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus

_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize_path_id(raw: str) -> str:
    """Make *raw* a safe storage key (matches ``LearningStore`` path guard)."""
    cleaned = _UNSAFE_ID_CHARS.sub("_", raw).strip("_")
    return cleaned or "default"


def _candidate_from_book_ref(ref: object) -> str:
    if isinstance(ref, str):
        return ref.strip()
    if isinstance(ref, dict):
        return str(ref.get("book_id") or ref.get("id") or "").strip()
    return ""


def _strip_kb_ref_prefix(value: str) -> str:
    candidate = value.strip()
    marker = ":kb:"
    if marker in candidate:
        return candidate.split(marker, 1)[1].strip()
    return candidate


def _candidate_from_kb_ref(ref: object) -> str:
    if isinstance(ref, str):
        return _strip_kb_ref_prefix(ref)
    if isinstance(ref, dict):
        candidate = str(ref.get("name") or ref.get("id") or "").strip()
        return _strip_kb_ref_prefix(candidate)
    return ""


def resolve_mastery_path_id(context: UnifiedContext) -> str:
    """Resolve which learner-path the turn operates on.

    Precedence is explicit public config, compatibility metadata, book
    reference, first selected knowledge base, then the session id. This keeps
    KB-named Mastery Paths deterministic for callers that only select a KB.
    """
    explicit_config = str((context.config_overrides or {}).get("mastery_path_id") or "").strip()
    if explicit_config:
        return _sanitize_path_id(explicit_config)

    explicit_metadata = str((context.metadata or {}).get("mastery_path_id") or "").strip()
    if explicit_metadata:
        return _sanitize_path_id(explicit_metadata)

    refs = (context.metadata or {}).get("book_references", [])
    if refs:
        candidate = _candidate_from_book_ref(refs[0])
        if candidate:
            return _sanitize_path_id(candidate)

    if context.knowledge_bases:
        candidate = _candidate_from_kb_ref(context.knowledge_bases[0])
        if candidate:
            return _sanitize_path_id(candidate)

    return _sanitize_path_id(str(context.session_id or "default"))


class MasteryPathCapability(BaseCapability):
    manifest = CapabilityManifest(
        name="mastery_path",
        description=(
            "Mastery-based tutoring: the chat agent loop drives an adaptive "
            "mastery path with a hard, per-type mastery gate and spaced review."
        ),
        stages=["responding"],
        tools_used=[*MASTERY_TOOL_NAMES, "rag", "read_source", "ask_user"],
        cli_aliases=["mastery"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        context.metadata["mastery_mode"] = True
        context.metadata["mastery_path_id"] = resolve_mastery_path_id(context)
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, stream)


__all__ = ["MasteryPathCapability", "resolve_mastery_path_id"]
