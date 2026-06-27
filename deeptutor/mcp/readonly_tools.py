"""Read-only DeepTutor tool implementations for the local MCP server."""

from __future__ import annotations

import asyncio
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel

from deeptutor.learning.models import LearningProgress
from deeptutor.learning.policy import map_summary, next_objective
from deeptutor.learning.storage import LearningStore
from deeptutor.services.session import get_session_store
from deeptutor.tools.rag_tool import rag_search

TOOL_NAMES = [
    "list_sessions",
    "get_session",
    "search_sessions",
    "get_turn_trace",
    "list_knowledge_bases",
    "search_kb",
    "list_mastery_paths",
    "get_mastery_path",
    "get_mastery_map",
]

_SECRET_KEY_RE = re.compile(
    r"(^|_)(api[_-]?key|token|secret|password|passwd|authorization|auth[_-]?header|bearer)($|_)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+\-/]+=*|\b(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,})\b",
    re.IGNORECASE,
)


def _clamp_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clip_text(value: object, max_chars: int) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    if max_chars < 0:
        max_chars = 0
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SECRET_KEY_RE.search(key_str):
                redacted[key_str] = "[redacted]"
            else:
                redacted[key_str] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(
            lambda m: f"{m.group(1)}[redacted]" if m.group(1) else "[redacted]",
            value,
        )
    return value


def _ok(data: dict[str, Any], **meta: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "data": _redact(_json_safe(data)),
        "meta": _redact(_json_safe(meta)),
    }


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(message),
            "details": _redact(_json_safe(details or {})),
        },
    }


def _exception_error(exc: Exception) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if status_code == 404:
        return _error("not_found", str(detail or exc))
    if status_code == 403:
        return _error("forbidden", str(detail or exc))
    if status_code == 400:
        return _error("invalid_argument", str(detail or exc))
    return _error("internal_error", str(detail or exc))


def _imported_session(row: dict[str, Any], explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return str(row.get("id") or row.get("session_id") or "").startswith("imported_")


def _format_session_summary(
    row: dict[str, Any], *, preview_chars: int, imported: bool | None = None
) -> dict[str, Any]:
    last_message, last_truncated = _clip_text(row.get("last_message", ""), preview_chars)
    summary, summary_truncated = _clip_text(row.get("compressed_summary", ""), preview_chars)
    session_id = str(row.get("id") or row.get("session_id") or "")
    return {
        "id": session_id,
        "session_id": session_id,
        "title": row.get("title") or "New conversation",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status") or "idle",
        "active_turn_id": row.get("active_turn_id") or "",
        "capability": row.get("capability") or "",
        "message_count": int(row.get("message_count") or 0),
        "last_message_preview": last_message,
        "last_message_truncated": last_truncated,
        "summary_preview": summary,
        "summary_truncated": summary_truncated,
        "imported": _imported_session(row, imported),
    }


async def _list_session_rows(
    *, source: str, limit: int, offset: int
) -> tuple[list[dict[str, Any]], bool]:
    store = get_session_store()
    fetch_count = max(1, limit + 1)
    normalized_source = source if source in {"native", "imported", "all"} else "native"
    if normalized_source == "native":
        rows = await store.list_sessions(limit=fetch_count, offset=offset)
        return rows[:limit], len(rows) > limit
    if normalized_source == "imported":
        imported_fn = getattr(store, "list_imported_sessions", None)
        if imported_fn is None:
            return [], False
        rows = await imported_fn(limit=fetch_count, offset=offset)
        return rows[:limit], len(rows) > limit

    scan_count = max(1, offset + limit + 1)
    rows = await store.list_sessions(limit=scan_count, offset=0)
    imported_fn = getattr(store, "list_imported_sessions", None)
    if imported_fn is not None:
        rows = [*rows, *(await imported_fn(limit=scan_count, offset=0))]
    rows.sort(key=lambda row: float(row.get("updated_at") or 0.0), reverse=True)
    page = rows[offset : offset + limit]
    return page, len(rows) > offset + limit


async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    source: str = "native",
    preview_chars: int = 300,
) -> dict[str, Any]:
    limit = _clamp_int(limit, default=20, minimum=1, maximum=100)
    offset = _clamp_int(offset, default=0, minimum=0, maximum=100_000)
    preview_chars = _clamp_int(preview_chars, default=300, minimum=0, maximum=1000)
    source = source if source in {"native", "imported", "all"} else "native"
    try:
        rows, truncated = await _list_session_rows(source=source, limit=limit, offset=offset)
        return _ok(
            {
                "sessions": [
                    _format_session_summary(row, preview_chars=preview_chars) for row in rows
                ]
            },
            limit=limit,
            offset=offset,
            source=source,
            truncated=truncated,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


def _format_event(
    event: dict[str, Any], *, include_content: bool = True, content_max_chars: int
) -> dict[str, Any]:
    content, truncated = _clip_text(event.get("content", ""), content_max_chars)
    payload = {
        "seq": event.get("seq"),
        "type": event.get("type") or "",
        "source": event.get("source") or "",
        "stage": event.get("stage") or "",
        "metadata": event.get("metadata") or {},
        "timestamp": event.get("timestamp"),
    }
    if include_content:
        payload["content"] = content
        payload["content_truncated"] = truncated
    return payload


def _format_message(
    message: dict[str, Any], *, include_events: bool, include_metadata: bool, content_max_chars: int
) -> dict[str, Any]:
    content, truncated = _clip_text(message.get("content", ""), content_max_chars)
    events = message.get("events") or []
    payload = {
        "id": message.get("id"),
        "role": message.get("role") or "",
        "content": content,
        "content_truncated": truncated,
        "capability": message.get("capability") or "",
        "created_at": message.get("created_at"),
        "parent_message_id": message.get("parent_message_id"),
        "attachments": message.get("attachments") or [],
    }
    if include_events:
        payload["events"] = [
            _format_event(event, content_max_chars=content_max_chars)
            for event in events
            if isinstance(event, dict)
        ]
    else:
        payload["event_count"] = len(events) if isinstance(events, list) else 0
    if include_metadata:
        payload["metadata"] = message.get("metadata") or {}
    return payload


async def get_session(
    session_id: str,
    message_limit: int = 100,
    message_offset: int = 0,
    include_events: bool = False,
    include_metadata: bool = False,
    content_max_chars: int = 8000,
) -> dict[str, Any]:
    session_id = str(session_id or "").strip()
    if not session_id:
        return _error("invalid_argument", "session_id is required")
    message_limit = _clamp_int(message_limit, default=100, minimum=1, maximum=300)
    message_offset = _clamp_int(message_offset, default=0, minimum=0, maximum=100_000)
    content_max_chars = _clamp_int(content_max_chars, default=8000, minimum=0, maximum=20000)
    try:
        payload = await get_session_store().get_session_with_messages(session_id)
        if payload is None:
            return _error("not_found", "Session not found", {"session_id": session_id})
        messages = list(payload.get("messages") or [])
        page = messages[message_offset : message_offset + message_limit]
        session = {
            "id": payload.get("id") or payload.get("session_id"),
            "session_id": payload.get("session_id") or payload.get("id"),
            "title": payload.get("title") or "New conversation",
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "compressed_summary": payload.get("compressed_summary") or "",
            "summary_up_to_msg_id": payload.get("summary_up_to_msg_id") or 0,
            "preferences": payload.get("preferences") or {},
            "active_turns": payload.get("active_turns") or [],
        }
        return _ok(
            {
                "session": session,
                "messages": [
                    _format_message(
                        message,
                        include_events=include_events,
                        include_metadata=include_metadata,
                        content_max_chars=content_max_chars,
                    )
                    for message in page
                    if isinstance(message, dict)
                ],
            },
            limit=message_limit,
            offset=message_offset,
            truncated=len(messages) > message_offset + message_limit,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


def _match_snippet(text: str, query: str, snippet_chars: int) -> str:
    idx = text.lower().find(query.lower())
    if idx < 0:
        snippet, _ = _clip_text(text, snippet_chars)
        return snippet
    start = max(0, idx - snippet_chars // 3)
    end = min(len(text), start + snippet_chars)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


async def search_sessions(
    query: str,
    limit: int = 10,
    source: str = "native",
    scan_limit: int = 300,
    snippet_chars: int = 240,
) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return _error("invalid_argument", "query is required")
    limit = _clamp_int(limit, default=10, minimum=1, maximum=50)
    scan_limit = _clamp_int(scan_limit, default=300, minimum=1, maximum=1000)
    snippet_chars = _clamp_int(snippet_chars, default=240, minimum=20, maximum=1000)
    source = source if source in {"native", "imported", "all"} else "native"
    try:
        rows, _ = await _list_session_rows(source=source, limit=scan_limit, offset=0)
        matches: list[dict[str, Any]] = []
        query_lower = query.lower()
        for row in rows:
            session_id = str(row.get("id") or row.get("session_id") or "")
            fields = {
                "title": row.get("title") or "",
                "summary": row.get("compressed_summary") or "",
                "last_message": row.get("last_message") or "",
            }
            matched_fields: list[str] = []
            snippets: list[dict[str, Any]] = []
            for field, text in fields.items():
                if query_lower in str(text).lower():
                    matched_fields.append(field)
                    snippets.append(
                        {
                            "field": field,
                            "message_id": None,
                            "text": _match_snippet(str(text), query, snippet_chars),
                        }
                    )
            detail = await get_session_store().get_session_with_messages(session_id)
            if detail:
                for message in detail.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    content = str(message.get("content") or "")
                    if query_lower in content.lower():
                        matched_fields.append("message.content")
                        snippets.append(
                            {
                                "field": "message.content",
                                "message_id": message.get("id"),
                                "text": _match_snippet(content, query, snippet_chars),
                            }
                        )
                        if len(snippets) >= 3:
                            break
            if matched_fields:
                matches.append(
                    {
                        "session_id": session_id,
                        "title": row.get("title") or "New conversation",
                        "score": float(len(set(matched_fields))),
                        "matched_fields": sorted(set(matched_fields)),
                        "snippets": snippets[:3],
                        "updated_at": row.get("updated_at"),
                        "imported": _imported_session(row),
                    }
                )
        matches.sort(
            key=lambda item: (item["score"], float(item.get("updated_at") or 0.0)),
            reverse=True,
        )
        return _ok({"matches": matches[:limit]}, limit=limit, truncated=len(matches) > limit)
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


async def get_turn_trace(
    turn_id: str,
    after_seq: int = 0,
    limit: int = 500,
    include_content: bool = True,
    content_max_chars: int = 4000,
) -> dict[str, Any]:
    turn_id = str(turn_id or "").strip()
    if not turn_id:
        return _error("invalid_argument", "turn_id is required")
    after_seq = _clamp_int(after_seq, default=0, minimum=0, maximum=1_000_000_000)
    limit = _clamp_int(limit, default=500, minimum=1, maximum=1000)
    content_max_chars = _clamp_int(content_max_chars, default=4000, minimum=0, maximum=12000)
    try:
        store = get_session_store()
        turn = await store.get_turn(turn_id)
        if turn is None:
            return _error("not_found", "Turn not found", {"turn_id": turn_id})
        events = await store.get_turn_events(turn_id, after_seq=after_seq)
        page = events[:limit]
        next_after_seq = None
        if len(events) > limit and page:
            next_after_seq = page[-1].get("seq")
        return _ok(
            {
                "turn": {
                    "id": turn.get("id") or turn.get("turn_id"),
                    "turn_id": turn.get("turn_id") or turn.get("id"),
                    "session_id": turn.get("session_id"),
                    "status": turn.get("status"),
                    "capability": turn.get("capability") or "",
                    "error": turn.get("error") or "",
                    "created_at": turn.get("created_at"),
                    "updated_at": turn.get("updated_at"),
                    "finished_at": turn.get("finished_at"),
                },
                "events": [
                    _format_event(
                        event,
                        include_content=include_content,
                        content_max_chars=content_max_chars,
                    )
                    for event in page
                    if isinstance(event, dict)
                ],
                "next_after_seq": next_after_seq,
            },
            limit=limit,
            after_seq=after_seq,
            truncated=len(events) > limit,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


def _kb_info_row(
    *,
    manager: Any,
    name: str,
    default_name: str | None,
    resource_id: str,
    source: str,
    assigned: bool,
    read_only: bool,
    provenance_label: str | None = None,
    available: bool = True,
) -> dict[str, Any]:
    try:
        info = manager.get_info(name, refresh=False, default_name=default_name)
        return {
            "id": resource_id,
            "name": str(info.get("name") or name),
            "is_default": bool(info.get("is_default", name == default_name)),
            "statistics": info.get("statistics") or {},
            "metadata": info.get("metadata") or {},
            "path": info.get("path"),
            "status": info.get("status"),
            "progress": info.get("progress"),
            "source": source,
            "assigned": assigned,
            "read_only": read_only,
            "provenance_label": provenance_label,
            "available": available,
        }
    except Exception as exc:
        kb_dir = getattr(manager, "base_dir", None)
        path = str(kb_dir / name) if kb_dir is not None else None
        return {
            "id": resource_id,
            "name": name,
            "is_default": name == default_name,
            "statistics": {},
            "metadata": {"name": name, "last_error": str(exc)},
            "path": path,
            "status": "error",
            "progress": {
                "stage": "error",
                "message": "Failed to load knowledge base info.",
                "error": str(exc),
            },
            "source": source,
            "assigned": assigned,
            "read_only": read_only,
            "provenance_label": provenance_label,
            "available": available,
        }


def _list_manager_kbs_sync(
    *, manager: Any, prefix: str, source: str, assigned: bool, read_only: bool
) -> list[dict[str, Any]]:
    try:
        manager.config = manager._load_config(reconcile=False)
    except TypeError:
        manager.config = manager._load_config()
    try:
        kb_names = manager.list_knowledge_bases(refresh=False)
    except TypeError:
        kb_names = manager.list_knowledge_bases()
    try:
        default_name = manager.get_default(refresh=False, kb_names=kb_names)
    except TypeError:
        default_name = manager.get_default()
    return [
        _kb_info_row(
            manager=manager,
            name=name,
            default_name=default_name,
            resource_id=f"{prefix}{name}",
            source=source,
            assigned=assigned,
            read_only=read_only,
            provenance_label="Admin workspace" if source == "admin" and not assigned else None,
        )
        for name in kb_names
    ]


def _list_knowledge_bases_sync() -> list[Any]:
    """List visible KBs without importing the heavy FastAPI knowledge router.

    The API route module imports document parsing dependencies (openpyxl/numpy)
    and loads ``main.yaml`` at import time.  Under stdio MCP on Windows that
    import can hang long enough for callers to time out before returning the
    same error envelope the direct tool path would produce.  The read-only MCP
    surface only needs service-layer KB summaries, so use the multi-user access
    layer and KnowledgeBaseManager directly.
    """
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.multi_user.knowledge_access import (
        ADMIN_PREFIX,
        USER_PREFIX,
        admin_kb_manager,
        current_kb_manager,
        list_visible_knowledge_bases,
        manager_for_resource,
        resolve_kb,
    )

    user = get_current_user()
    if user.is_admin:
        return _list_manager_kbs_sync(
            manager=admin_kb_manager(),
            prefix=ADMIN_PREFIX,
            source="admin",
            assigned=False,
            read_only=False,
        )

    rows = _list_manager_kbs_sync(
        manager=current_kb_manager(),
        prefix=USER_PREFIX,
        source="user",
        assigned=False,
        read_only=False,
    )
    own_ids = {str(row.get("id") or "") for row in rows}
    for access in list_visible_knowledge_bases():
        resource_id = str(access.get("id") or "")
        if not access.get("assigned") or resource_id in own_ids:
            continue
        if not access.get("available", True):
            rows.append(
                {
                    "id": resource_id,
                    "name": str(access.get("name") or ""),
                    "is_default": False,
                    "statistics": {},
                    "metadata": {},
                    "path": None,
                    "status": "unavailable",
                    "progress": None,
                    "source": str(access.get("source") or "admin"),
                    "assigned": True,
                    "read_only": True,
                    "provenance_label": str(access.get("provenance_label") or ""),
                    "available": False,
                }
            )
            continue
        resource = resolve_kb(resource_id or str(access.get("name") or ""))
        rows.append(
            _kb_info_row(
                manager=manager_for_resource(resource),
                name=resource.name,
                default_name=None,
                resource_id=resource.id,
                source=resource.source,
                assigned=True,
                read_only=True,
                provenance_label=str(access.get("provenance_label") or ""),
            )
        )
    return rows


async def list_knowledge_bases(
    include_assigned: bool = True,
    include_unavailable: bool = False,
    include_path: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    limit = _clamp_int(limit, default=100, minimum=1, maximum=500)
    offset = _clamp_int(offset, default=0, minimum=0, maximum=100_000)
    try:
        rows = await asyncio.to_thread(_list_knowledge_bases_sync)
        items: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe(row)
            if not isinstance(item, dict):
                continue
            if not include_assigned and item.get("assigned"):
                continue
            if not include_unavailable and item.get("available") is False:
                continue
            if not include_path:
                item.pop("path", None)
            items.append(item)
        page = items[offset : offset + limit]
        return _ok(
            {"knowledge_bases": page},
            limit=limit,
            offset=offset,
            truncated=len(items) > offset + limit,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


async def search_kb(
    kb_name: str,
    query: str,
    limit: int = 5,
    mode: str = "",
    include_answer: bool = False,
    content_max_chars: int = 12000,
) -> dict[str, Any]:
    kb_name = str(kb_name or "").strip()
    query = str(query or "").strip()
    if not kb_name:
        return _error("invalid_argument", "kb_name is required")
    if not query:
        return _error("invalid_argument", "query is required")
    limit = _clamp_int(limit, default=5, minimum=1, maximum=20)
    content_max_chars = _clamp_int(content_max_chars, default=12000, minimum=0, maximum=30000)
    try:
        kwargs: dict[str, Any] = {"top_k": limit}
        if mode:
            kwargs["mode"] = mode
        result = await rag_search(query=query, kb_name=kb_name, **kwargs)
        if result.get("error_type") or result.get("needs_reindex"):
            return _error(
                "provider_unavailable",
                str(result.get("answer") or result.get("content") or "RAG provider unavailable"),
                {
                    "provider": result.get("provider"),
                    "error_type": result.get("error_type"),
                    "needs_reindex": bool(result.get("needs_reindex")),
                },
            )
        content_source = result.get("content")
        warnings: list[str] = []
        if not content_source and result.get("answer"):
            content_source = result.get("answer")
            warnings.append("provider returned answer without separate retrieval content")
        content, content_truncated = _clip_text(content_source or "", content_max_chars)
        sources = result.get("sources") or []
        data = {
            "kb_name": kb_name,
            "query": result.get("query") or query,
            "provider": result.get("provider") or "",
            "content": content,
            "content_truncated": content_truncated,
            "sources": sources[:limit] if isinstance(sources, list) else [],
            "warnings": warnings,
        }
        if include_answer and result.get("answer") is not None:
            answer, answer_truncated = _clip_text(result.get("answer") or "", content_max_chars)
            data["answer"] = answer
            data["answer_truncated"] = answer_truncated
        return _ok(data, limit=limit)
    except ValueError as exc:
        return _error("not_found", str(exc))
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


def _load_progress(path_id: str) -> tuple[LearningProgress | None, dict[str, Any] | None]:
    try:
        return LearningStore().load(path_id), None
    except ValueError as exc:
        return None, _error("invalid_argument", str(exc))


def _kp_count(progress: LearningProgress) -> int:
    return sum(len(module.knowledge_points) for module in progress.modules)


def _avg_mastery(progress: LearningProgress) -> float:
    kp_ids = [kp.id for module in progress.modules for kp in module.knowledge_points]
    if not kp_ids:
        return 0.0
    return round(
        sum(float(progress.mastery_levels.get(kp_id, 0.0)) for kp_id in kp_ids)
        / len(kp_ids),
        3,
    )


def _summarize_progress(progress: LearningProgress) -> dict[str, Any]:
    return {
        "path_id": progress.book_id,
        "book_id": progress.book_id,
        "name": progress.modules[0].name if progress.modules else progress.book_id,
        "modules_count": len(progress.modules),
        "kp_count": _kp_count(progress),
        "current_stage": progress.current_stage.value,
        "current_module_id": progress.current_module_id,
        "current_kp_index": progress.current_kp_index,
        "avg_mastery_pct": round(_avg_mastery(progress) * 100, 1),
        "version": progress.version,
        "created_at": progress.created_at,
        "updated_at": progress.updated_at,
    }


async def list_mastery_paths(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit = _clamp_int(limit, default=100, minimum=1, maximum=500)
    offset = _clamp_int(offset, default=0, minimum=0, maximum=100_000)
    try:
        store = LearningStore()
        ids = store.list_all()
        page_ids = ids[offset : offset + limit]
        paths: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for path_id in page_ids:
            try:
                progress = store.load(path_id)
                if progress is not None:
                    paths.append(_summarize_progress(progress))
            except Exception as exc:  # pragma: no cover - corrupt local file
                errors.append({"path_id": path_id, "error": str(exc)})
        return _ok(
            {"paths": paths, "errors": errors},
            limit=limit,
            offset=offset,
            truncated=len(ids) > offset + limit,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


def _progress_payload(
    progress: LearningProgress,
    *,
    include_attempts: bool,
    attempt_limit: int,
    include_private_answers: bool,
) -> dict[str, Any]:
    payload = progress.model_dump(mode="json")
    payload["path_id"] = progress.book_id
    if not include_attempts:
        payload["quiz_attempts"] = []
        payload["quiz_attempt_count"] = len(progress.quiz_attempts)
    else:
        payload["quiz_attempts"] = payload.get("quiz_attempts", [])[:attempt_limit]
        payload["quiz_attempt_count"] = len(progress.quiz_attempts)
        payload["quiz_attempts_truncated"] = len(progress.quiz_attempts) > attempt_limit
    pending = payload.get("pending_question")
    if isinstance(pending, dict) and not include_private_answers:
        expected = pending.pop("expected_answer", "")
        pending["has_expected_answer"] = bool(expected)
    return payload


async def get_mastery_path(
    path_id: str,
    include_attempts: bool = False,
    attempt_limit: int = 50,
    include_private_answers: bool = False,
) -> dict[str, Any]:
    path_id = str(path_id or "").strip()
    if not path_id:
        return _error("invalid_argument", "path_id is required")
    attempt_limit = _clamp_int(attempt_limit, default=50, minimum=0, maximum=200)
    try:
        progress, error = _load_progress(path_id)
        if error is not None:
            return error
        if progress is None:
            return _error("not_found", "Mastery path not found", {"path_id": path_id})
        return _ok(
            {
                "path": _progress_payload(
                    progress,
                    include_attempts=include_attempts,
                    attempt_limit=attempt_limit,
                    include_private_answers=include_private_answers,
                )
            },
            attempt_limit=attempt_limit,
            attempts_truncated=include_attempts and len(progress.quiz_attempts) > attempt_limit,
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


async def get_mastery_map(path_id: str) -> dict[str, Any]:
    path_id = str(path_id or "").strip()
    if not path_id:
        return _error("invalid_argument", "path_id is required")
    try:
        progress, error = _load_progress(path_id)
        if error is not None:
            return error
        if progress is None:
            return _error("not_found", "Mastery path not found", {"path_id": path_id})
        return _ok(
            {
                "path_id": progress.book_id,
                "next": next_objective(progress).to_dict(),
                "map": map_summary(progress),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive envelope
        return _exception_error(exc)


__all__ = [
    "TOOL_NAMES",
    "get_mastery_map",
    "get_mastery_path",
    "get_session",
    "get_turn_trace",
    "list_knowledge_bases",
    "list_mastery_paths",
    "list_sessions",
    "search_kb",
    "search_sessions",
]
