"""Read-only DeepTutor MCP server and tool contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from deeptutor.learning.storage import LearningStore
from deeptutor.mcp import readonly_tools
from deeptutor.mcp.readonly_server import create_server
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.mark.asyncio
async def test_readonly_server_lists_exact_v1_tool_names() -> None:
    server = create_server()

    listed = await server.list_tools()

    assert {tool.name for tool in listed} == set(readonly_tools.TOOL_NAMES)
    assert len(listed) == 9
    assert "deeptutor_list_sessions" not in {tool.name for tool in listed}


@pytest.mark.asyncio
async def test_readonly_session_tools_return_capped_session_and_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    monkeypatch.setattr(readonly_tools, "get_session_store", lambda: store)

    await store.create_session(title="Maintenance Forms", session_id="session-demo")
    turn = await store.create_turn("session-demo", capability="chat")
    await store.append_turn_event(
        turn["id"],
        {
            "type": "status",
            "source": "rag",
            "stage": "retrieving",
            "content": "Retrieved maintenance context",
            "metadata": {"api_key": "sk-secretsecretsecret"},
        },
    )
    await store.update_turn_status(turn["id"], "completed")
    await store.add_message(
        "session-demo",
        "user",
        "Explain maintenance forms in Acumatica",
        capability="chat",
        metadata={"api_key": "sk-secretsecretsecret"},
    )

    listed = await readonly_tools.list_sessions(limit=5, preview_chars=12)
    fetched = await readonly_tools.get_session(
        "session-demo", include_metadata=True, content_max_chars=20
    )
    matches = await readonly_tools.search_sessions("maintenance")
    trace = await readonly_tools.get_turn_trace(turn["id"])

    assert listed["ok"] is True
    assert listed["data"]["sessions"][0]["id"] == "session-demo"
    assert fetched["ok"] is True
    assert fetched["data"]["messages"][0]["content_truncated"] is True
    assert fetched["data"]["messages"][0]["metadata"]["api_key"] == "[redacted]"
    assert matches["data"]["matches"][0]["session_id"] == "session-demo"
    assert trace["ok"] is True
    assert trace["data"]["events"][0]["content"] == "Retrieved maintenance context"
    assert trace["data"]["events"][0]["metadata"]["api_key"] == "[redacted]"


@pytest.mark.asyncio
async def test_readonly_kb_tools_list_and_search_with_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readonly_tools,
        "_list_knowledge_bases_sync",
        lambda: [
            {
                "id": "admin:kb:acumatica-courses-lightrag",
                "name": "acumatica-courses-lightrag",
                "is_default": False,
                "statistics": {"raw_documents": 1},
                "metadata": {"rag_provider": "fake"},
                "path": "C:/private/path",
                "status": "ready",
                "progress": {"stage": "completed", "percent": 100},
                "source": "admin",
                "assigned": False,
                "read_only": False,
                "available": True,
            }
        ],
    )

    async def fake_rag_search(**kwargs):
        return {
            "query": kwargs["query"],
            "content": "context " * 20,
            "answer": "private synthesized answer",
            "sources": [
                {"title": "T200 Maintenance Forms", "score": 0.9},
                {"title": "Ignored by limit", "score": 0.1},
            ],
            "provider": "fake",
        }

    monkeypatch.setattr(readonly_tools, "rag_search", fake_rag_search)

    listed = await readonly_tools.list_knowledge_bases(include_path=False)
    search = await readonly_tools.search_kb(
        "acumatica-courses-lightrag", "maintenance forms", limit=1, content_max_chars=16
    )

    assert listed["ok"] is True
    kb = listed["data"]["knowledge_bases"][0]
    assert kb["name"] == "acumatica-courses-lightrag"
    assert "path" not in kb
    assert search["ok"] is True
    assert search["data"]["provider"] == "fake"
    assert search["data"]["content_truncated"] is True
    assert len(search["data"]["sources"]) == 1
    assert "answer" not in search["data"]


@pytest.mark.asyncio
async def test_readonly_kb_list_handles_empty_runtime_without_main_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from deeptutor.multi_user import grants, paths

    project_root = tmp_path
    admin_root = (project_root / "data").resolve()
    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(paths, "USERS_ROOT", admin_root / "users")
    monkeypatch.setattr(paths, "SYSTEM_ROOT", admin_root / "system")
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", project_root / "multi-user")
    monkeypatch.setattr(paths, "_path_services", {})
    monkeypatch.setattr(grants, "GRANTS_DIR", admin_root / "system" / "grants")
    from deeptutor.multi_user import knowledge_access

    knowledge_access._manager_for.cache_clear()

    listed = await readonly_tools.list_knowledge_bases(include_path=False)

    assert listed["ok"] is True
    assert listed["data"]["knowledge_bases"] == []


@pytest.mark.asyncio
async def test_readonly_mastery_tools_do_not_create_missing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LearningStore(root=tmp_path)
    monkeypatch.setattr(readonly_tools, "LearningStore", lambda: store)
    progress = LearningProgress(
        book_id="acumatica-courses-lightrag",
        current_module_id="acumatica-courses-lightrag_m0",
        modules=[
            LearningModule(
                id="acumatica-courses-lightrag_m0",
                name="T200 Maintenance Forms",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="acumatica-courses-lightrag_m0_kp0",
                        name="Screen layout",
                        type=KnowledgeType.CONCEPT,
                        module_id="acumatica-courses-lightrag_m0",
                    )
                ],
            )
        ],
        pending_question=PendingQuestion(
            question_id="q1",
            knowledge_point_id="acumatica-courses-lightrag_m0_kp0",
            module_id="acumatica-courses-lightrag_m0",
            prompt="Explain the layout.",
            expected_answer="private rubric",
        ),
    )
    store.save(progress)

    listed = await readonly_tools.list_mastery_paths()
    fetched = await readonly_tools.get_mastery_path("acumatica-courses-lightrag")
    mastery_map = await readonly_tools.get_mastery_map("acumatica-courses-lightrag")
    missing = await readonly_tools.get_mastery_path("missing-path")

    assert listed["data"]["paths"][0]["path_id"] == "acumatica-courses-lightrag"
    pending = fetched["data"]["path"]["pending_question"]
    assert pending["has_expected_answer"] is True
    assert "expected_answer" not in pending
    assert mastery_map["data"]["next"]["action"] == "answer_pending"
    assert missing["ok"] is False
    assert missing["error"]["code"] == "not_found"
    assert not (tmp_path / "missing-path.json").exists()
