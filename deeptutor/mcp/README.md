# DeepTutor read-only MCP v1 and Mastery Path routing contract

Status: D0 specification for implementation
Date: 2026-06-25
Scope: local DeepTutor runtime data exposed to Hermes through a stable read-only MCP server, plus deterministic Mastery Path path-id routing.

## Problem statement

Hermes integrations need a stable DeepTutor tool surface for sessions, knowledge bases, retrieval results, and Mastery Path state. They should not scrape DeepTutor's SQLite tables or `learning/*.json` progress files directly. The first MCP version must be local and read-only by default.

A related routing bug exists in the current Mastery Path capability: the active path id is resolved from `context.metadata["mastery_path_id"]`, then `book_references`, then `session_id` in `deeptutor/capabilities/mastery/capability.py::resolve_mastery_path_id`. Public `config.mastery_path_id` and selected knowledge-base names are not part of the current precedence chain, so KB-named paths such as `acumatica-courses-lightrag` are not deterministic unless the caller manually injects metadata.

## Existing implementation anchors

Use these code paths as implementation sources of truth:

| Area | Current files/functions |
| --- | --- |
| Session list/detail/message state | `deeptutor/services/session/protocol.py::SessionStore`, `deeptutor/services/session/sqlite_store.py::list_sessions`, `list_imported_sessions`, `get_session_with_messages`, `get_turn_events`; HTTP read endpoints in `deeptutor/api/routers/sessions.py::list_sessions` and `get_session`. |
| Turn traces | `deeptutor/services/session/sqlite_store.py::_get_turn_events_sync` and `get_turn_events`; runtime streaming in `deeptutor/services/session/turn_runtime.py::subscribe_turn`. |
| Knowledge-base listing/status | `deeptutor/api/routers/knowledge.py::list_knowledge_bases`, `_list_knowledge_bases_sync`, `get_knowledge_base_details`, `get_progress`; resource access helpers in `deeptutor/multi_user/knowledge_access.py`. |
| Retrieval/search | `deeptutor/tools/rag_tool.py::rag_search`; `deeptutor/services/rag/service.py::RAGService.search`; pipeline contract in `deeptutor/services/rag/pipelines/base.py` returning at least `query`, `content`/`answer`, `sources`, and `provider`. |
| Mastery progress/map | `deeptutor/learning/storage.py::LearningStore`, `deeptutor/learning/service.py::LearningService.list_progress`, `deeptutor/learning/models.py::LearningProgress`, `deeptutor/api/routers/mastery_path.py::list_all_progress`, `get_progress`, `get_progress_map`. |
| Mastery routing | `deeptutor/capabilities/mastery/capability.py::resolve_mastery_path_id` and `MasteryPathCapability.run`; `deeptutor/services/session/turn_runtime.py` constructs `UnifiedContext` with `knowledge_bases`, `config_overrides`, and metadata. |
| Request config validation | `deeptutor/runtime/request_contracts.py::validate_capability_config` and `CAPABILITY_CONFIG_VALIDATORS`. |
| DeepTutor MCP client settings | `deeptutor/services/mcp/config.py::MCPServerConfig`, `MCPConfig`, `load_mcp_config`, `save_mcp_config`; these are for DeepTutor-as-client and should not be confused with this new DeepTutor-as-server surface. |

## Implementation decision: stdio MCP server using the MCP SDK

Recommended first implementation: a repo-owned stdio MCP server using the existing `mcp` SDK, exposed as:

- `python -m deeptutor.mcp.readonly_server`
- optional source-checkout command: `uv run python -m deeptutor.mcp.readonly_server`

Rationale:

1. Hermes native MCP already supports stdio servers with `command`/`args`.
2. Stdio keeps v1 local-first and avoids requiring the FastAPI backend to be running.
3. The repo already carries `mcp>=1.26.0,<2.0.0` for partner/MCP client support; using the MCP SDK avoids adding a new `fastmcp` runtime dependency to the core application.
4. HTTP/StreamableHTTP can be added later when auth, remote access, and multi-tenant scope boundaries are designed. It should not be v1 because the read-only server is intended for local Hermes access to the same runtime data root.
5. FastMCP remains a good prototyping tool, but adopting it in production would require an explicit dependency decision and should not block the smaller SDK stdio implementation.

## Naming convention

Expose short raw MCP tool names from the DeepTutor server. With Hermes configured as `mcp_servers.deeptutor`, Hermes will register them as `mcp_deeptutor_<tool>`.

Do not prefix raw tools with `deeptutor_` in v1. That would produce duplicated Hermes-visible names such as `mcp_deeptutor_deeptutor_list_sessions`.

Canonical v1 tools:

| Raw MCP tool | Hermes-visible name with `mcp_servers.deeptutor` |
| --- | --- |
| `list_sessions` | `mcp_deeptutor_list_sessions` |
| `get_session` | `mcp_deeptutor_get_session` |
| `search_sessions` | `mcp_deeptutor_search_sessions` |
| `get_turn_trace` | `mcp_deeptutor_get_turn_trace` |
| `list_knowledge_bases` | `mcp_deeptutor_list_knowledge_bases` |
| `search_kb` | `mcp_deeptutor_search_kb` |
| `list_mastery_paths` | `mcp_deeptutor_list_mastery_paths` |
| `get_mastery_path` | `mcp_deeptutor_get_mastery_path` |
| `get_mastery_map` | `mcp_deeptutor_get_mastery_map` |

## Common MCP response and safety rules

All tools return JSON-safe dictionaries:

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "limit": 20,
    "truncated": false
  }
}
```

Expected validation and not-found failures return:

```json
{
  "ok": false,
  "error": {
    "code": "not_found",
    "message": "Session not found",
    "details": {}
  }
}
```

Unexpected exceptions should be raised as sanitized MCP tool errors after redacting likely secrets from messages and metadata.

Global caps and sanitization:

- `limit` parameters are clamped to each tool's documented maximum.
- Text fields include `*_truncated` booleans when clipped.
- Default preview cap: 500 chars. Default full content cap: 8,000 chars per message/result unless a smaller tool-specific cap applies.
- Metadata is returned only when useful, redacted, and capped. Never return raw environment values, API keys, bearer tokens, passwords, connection strings, or auth headers.
- Attachment records are metadata only; do not return raw file bytes or base64 payloads.
- Mastery pending-question `expected_answer` is private tutor state and must be redacted by default.
- Read-only means no calls to `LearningService.get_or_create` for missing paths, no session creation, no progress creation, no KB indexing/reindexing, no writes to settings, no progress clearing, no deletes.

Suggested helper locations:

- `deeptutor/mcp/readonly_server.py` for MCP server registration.
- `deeptutor/mcp/readonly_tools.py` for plain Python functions used by both MCP handlers and tests.
- If router logic must be shared, extract read-only helpers from routers into service modules rather than importing FastAPI route handlers into the MCP server.

## Tool contracts

### `list_sessions`

Purpose: list chat sessions without message bodies.

Parameters:

- `limit: int = 20`, max 100.
- `offset: int = 0`.
- `source: "native" | "imported" | "all" = "native"`.
- `preview_chars: int = 300`, max 1000.

Implementation:

- Use `get_session_store().list_sessions(limit, offset)` for native sessions.
- Use `list_imported_sessions(limit, offset)` for imported sessions if `source` includes imported.
- Do not query SQLite directly from the MCP layer.

Return data:

```json
{
  "sessions": [
    {
      "id": "session-id",
      "title": "New conversation",
      "created_at": 1710000000.0,
      "updated_at": 1710000001.0,
      "status": "idle",
      "active_turn_id": "",
      "capability": "chat",
      "message_count": 6,
      "last_message_preview": "...",
      "last_message_truncated": false,
      "summary_preview": "...",
      "summary_truncated": false,
      "imported": false
    }
  ]
}
```

### `get_session`

Purpose: fetch one session and capped messages.

Parameters:

- `session_id: str` required.
- `message_limit: int = 100`, max 300.
- `message_offset: int = 0`.
- `include_events: bool = false`.
- `include_metadata: bool = false`.
- `content_max_chars: int = 8000`, max 20000.

Implementation:

- Use `get_session_store().get_session_with_messages(session_id)`.
- Apply message slicing and clipping in the MCP helper.
- `include_events=false` should omit message `events` arrays and return `event_count` instead.

Return data:

```json
{
  "session": {
    "id": "session-id",
    "title": "...",
    "created_at": 1710000000.0,
    "updated_at": 1710000001.0,
    "compressed_summary": "...",
    "preferences": {}
  },
  "messages": [
    {
      "id": 12,
      "role": "user",
      "content": "...",
      "content_truncated": false,
      "capability": "chat",
      "created_at": 1710000000.0,
      "parent_message_id": null,
      "attachments": [],
      "event_count": 0
    }
  ]
}
```

Error behavior:

- Missing session returns `ok=false`, `error.code="not_found"`.

### `search_sessions`

Purpose: search recent session titles, summaries, last messages, and capped message text through service/store APIs.

Parameters:

- `query: str` required, non-empty.
- `limit: int = 10`, max 50.
- `source: "native" | "imported" | "all" = "native"`.
- `scan_limit: int = 300`, max 1000.
- `snippet_chars: int = 240`, max 1000.

Implementation:

- For v1, scan summaries returned by `list_sessions` / `list_imported_sessions`, then fetch candidate sessions with `get_session_with_messages` until `scan_limit` is reached.
- Match case-insensitively across title, compressed summary, last message, and message content.
- Do not add a public DB-schema dependency. A future v2 may add a `SessionStore.search_sessions` FTS-backed service method.

Return data:

```json
{
  "matches": [
    {
      "session_id": "session-id",
      "title": "...",
      "score": 1.0,
      "matched_fields": ["title", "message.content"],
      "snippets": [
        {"field": "message.content", "message_id": 12, "text": "..."}
      ],
      "updated_at": 1710000001.0
    }
  ]
}
```

### `get_turn_trace`

Purpose: fetch persisted streaming/tool events for a turn.

Parameters:

- `turn_id: str` required.
- `after_seq: int = 0`.
- `limit: int = 500`, max 1000.
- `include_content: bool = true`.
- `content_max_chars: int = 4000`, max 12000.

Implementation:

- Use `get_session_store().get_turn(turn_id)` to validate existence.
- Use `get_session_store().get_turn_events(turn_id, after_seq=after_seq)`.
- Slice to `limit`; return `next_after_seq` when more data exists.

Return data:

```json
{
  "turn": {"id": "turn-id", "session_id": "session-id", "status": "completed", "capability": "chat"},
  "events": [
    {
      "seq": 1,
      "type": "status",
      "source": "rag",
      "stage": "retrieving",
      "content": "...",
      "content_truncated": false,
      "metadata": {},
      "timestamp": 1710000000.0
    }
  ],
  "next_after_seq": 501
}
```

### `list_knowledge_bases`

Purpose: list KBs and status visible to the current local/admin context.

Parameters:

- `include_assigned: bool = true`.
- `include_unavailable: bool = false`.
- `include_path: bool = false` (default false to avoid leaking local layout unless explicitly requested).
- `limit: int = 100`, max 500.
- `offset: int = 0`.

Implementation:

- Extract or reuse the read-only logic from `deeptutor/api/routers/knowledge.py::_list_knowledge_bases_sync`.
- Preserve multi-user access semantics from `resolve_kb`, `manager_for_resource`, and `list_visible_kb_access`.
- Do not expose writable KB operations.

Return data:

```json
{
  "knowledge_bases": [
    {
      "id": "admin:kb:acumatica-courses-lightrag",
      "name": "acumatica-courses-lightrag",
      "is_default": false,
      "source": "admin",
      "assigned": false,
      "read_only": false,
      "available": true,
      "status": "ready",
      "statistics": {"raw_documents": 1, "rag_initialized": true},
      "progress": {"stage": "completed", "percent": 100},
      "metadata": {"rag_provider": "lightrag"}
    }
  ]
}
```

### `search_kb`

Purpose: run a capped read-only retrieval query against one accessible KB.

Parameters:

- `kb_name: str` required.
- `query: str` required, non-empty.
- `limit: int = 5`, max 20. Pass through as `top_k` or provider-equivalent where supported.
- `mode: str = ""` optional provider-specific query mode.
- `include_answer: bool = false`.
- `content_max_chars: int = 12000`, max 30000.

Implementation:

- Use `deeptutor.tools.rag_tool.rag_search(query, kb_name, **kwargs)` or `RAGService.search` after resolving the KB through `resolve_for_rag`.
- Return `provider` exactly as resolved by `RAGService.search`.
- Prefer retrieval/context fields and normalized source records. If a provider only exposes an answer-style string, return it as `content` with a warning.
- `include_answer=false` should omit or clip synthesized answer text when context/source snippets are available. This avoids turning a read-only inspection tool into an expensive answer generator by default, while still supporting current providers whose only public contract is `content`/`answer`.

Return data:

```json
{
  "kb_name": "acumatica-courses-lightrag",
  "query": "maintenance forms",
  "provider": "lightrag",
  "content": "retrieved context or clipped provider answer",
  "content_truncated": false,
  "sources": [
    {"title": "T200 Maintenance Forms", "path": "...", "score": 0.82, "metadata": {}}
  ],
  "warnings": []
}
```

Error behavior:

- Empty query or KB name returns `invalid_argument`.
- Inaccessible/missing KB returns `not_found` or `forbidden` depending on `resolve_for_rag` result.
- Provider readiness errors should surface as `provider_unavailable` with the provider's `needs_reindex` flag if present.

### `list_mastery_paths`

Purpose: list stored Mastery Path progress files without creating new progress.

Parameters:

- `limit: int = 100`, max 500.
- `offset: int = 0`.

Implementation:

- Use `LearningStore.list_all()` plus `LearningStore.load()` directly, or `LearningService.list_progress()` if it does not create paths.
- Do not call `LearningService.get_or_create` here.

Return data:

```json
{
  "paths": [
    {
      "path_id": "acumatica-courses-lightrag",
      "name": "T200 Maintenance Forms",
      "modules_count": 8,
      "kp_count": 42,
      "current_stage": "diagnostic",
      "avg_mastery_pct": 0,
      "updated_at": 1710000000.0
    }
  ],
  "errors": []
}
```

### `get_mastery_path`

Purpose: fetch one persisted LearningProgress object with safe redactions and caps.

Parameters:

- `path_id: str` required.
- `include_attempts: bool = false`.
- `attempt_limit: int = 50`, max 200.
- `include_private_answers: bool = false`; default false and should remain false for Hermes/user-facing deployments.

Implementation:

- Validate `path_id` with the same storage guard as `LearningStore._path`: reject `/`, `\\`, `..`, and `:`.
- Use `LearningStore.load(path_id)`.
- Do not call `LearningService.get_or_create`; missing path returns `not_found` and creates no file.

Return data:

```json
{
  "path": {
    "path_id": "acumatica-courses-lightrag",
    "current_module_id": "acumatica-courses-lightrag_m0",
    "current_stage": "diagnostic",
    "current_kp_index": 0,
    "version": 3,
    "created_at": 1710000000.0,
    "updated_at": 1710000001.0,
    "modules": [
      {
        "id": "..._m0",
        "name": "Module name",
        "order": 0,
        "pass_threshold": 0.7,
        "knowledge_points": [
          {"id": "..._kp0", "name": "Objective", "type": "concept", "module_id": "..._m0"}
        ]
      }
    ],
    "mastery_levels": {},
    "qualitative_mastery": {},
    "knowledge_types": {},
    "review_queue": [],
    "pending_question": {"question_id": "q1", "prompt": "...", "has_expected_answer": true}
  }
}
```

### `get_mastery_map`

Purpose: fetch the dashboard-style next objective and objective map for one persisted path.

Parameters:

- `path_id: str` required.

Implementation:

- Use `LearningStore.load(path_id)`.
- If present, call `learning_policy.next_objective(progress).to_dict()` and `learning_policy.map_summary(progress)`.
- Do not call `LearningService.get_or_create`; missing path returns `not_found`.

Return data:

```json
{
  "path_id": "acumatica-courses-lightrag",
  "next": {},
  "map": []
}
```

## Mastery Path routing fix

Current behavior in `resolve_mastery_path_id(context)`:

1. `context.metadata["mastery_path_id"]`
2. first `metadata["book_references"]` book id/id
3. `context.session_id`

Required v1 behavior:

1. `context.config_overrides["mastery_path_id"]`
2. `context.metadata["mastery_path_id"]`
3. first `context.metadata["book_references"]` `book_id`/`id` or string reference
4. first `context.knowledge_bases` item, using string value or dict `name`/`id` with `admin:kb:` or `user:kb:` prefix stripped
5. `context.session_id`
6. `default`

All candidates are sanitized with the existing `_sanitize_path_id` behavior before returning.

Required code changes:

- `deeptutor/runtime/request_contracts.py`
  - Add `MasteryPathRequestConfig` with `mastery_path_id: str = ""` and `extra="forbid"`.
  - Register it in `CAPABILITY_CONFIG_VALIDATORS` and `CAPABILITY_REQUEST_SCHEMAS` for capability `"mastery_path"`.
- `deeptutor/capabilities/mastery/capability.py::resolve_mastery_path_id`
  - Implement the precedence above.
  - Add helper parsing for KB references so `"acumatica-courses-lightrag"`, `"admin:kb:acumatica-courses-lightrag"`, and `{ "name": "acumatica-courses-lightrag" }` resolve to the same sanitized path id.
- `deeptutor/services/session/turn_runtime.py`
  - No required mutation if `resolve_mastery_path_id` reads `context.config_overrides` and `context.knowledge_bases`, because `_run_turn` already passes `config_overrides=request_config` and `knowledge_bases=payload.get("knowledge_bases", [])` into `UnifiedContext`.
  - Optional: include `"mastery_path_id": request_config.get("mastery_path_id", "")` in context metadata for trace/debug visibility, but avoid making metadata the only path.

## Native Hermes config example

Installed package or active venv:

```yaml
mcp_servers:
  deeptutor:
    command: "python"
    args: ["-m", "deeptutor.mcp.readonly_server"]
    env:
      DEEPTUTOR_HOME: "C:\\path\\to\\DeepTutor-runtime-home"
    timeout: 120
    connect_timeout: 60
```

Source checkout using uv:

```yaml
mcp_servers:
  deeptutor:
    command: "uv"
    args: ["run", "python", "-m", "deeptutor.mcp.readonly_server"]
    env:
      DEEPTUTOR_HOME: "C:\\path\\to\\DeepTutor-runtime-home"
    timeout: 120
    connect_timeout: 60
```

`DEEPTUTOR_HOME` selects the runtime home used by `deeptutor/runtime/home.py`; runtime data lives below `<DEEPTUTOR_HOME>/data`. The MCP server should not require API keys for local read-only inspection. If future HTTP transport is added, authentication and remote authorization must be specified separately.

## Test plan for the implementation card

Mastery routing:

- Extend `tests/capabilities/test_mastery_path_capability.py`:
  - config override wins over metadata, book refs, KBs, and session id.
  - metadata wins over book refs, KBs, and session id.
  - book refs win over KBs and session id.
  - first KB name wins over session id.
  - prefixed KB IDs (`admin:kb:<name>`, `user:kb:<name>`) sanitize to `<name>`.
- Add/extend request contract tests for `validate_capability_config("mastery_path", {"mastery_path_id": "acumatica-courses-lightrag"})`.
- Run: `pytest tests/capabilities/test_mastery_path_capability.py` plus the request-contract tests.

MCP server:

- Add `tests/services/mcp/test_readonly_server.py` or equivalent.
- Import test: `python -m deeptutor.mcp.readonly_server --help` or a direct server import must not initialize writable state.
- Discovery/list test: instantiate the server or use MCP SDK test client and assert the nine raw tool names are present.
- Representative in-process calls using temporary runtime data:
  - Sessions: create a temporary `SqliteSessionStore`, add one session/message/turn event, call `list_sessions`, `get_session`, and `get_turn_trace`.
  - KBs: monkeypatch path/Known KB manager helpers or create a minimal KB config/status fixture, call `list_knowledge_bases`; for `search_kb`, monkeypatch `rag_search`/`RAGService.search` to return a provider result and assert normalization/capping.
  - Mastery: create a temporary `LearningStore` root with one `LearningProgress`, call `list_mastery_paths`, `get_mastery_path`, and `get_mastery_map`; assert missing path does not create a file.
- Targeted test command should include:
  - `pytest tests/services/mcp/test_mcp_config.py tests/capabilities/test_mastery_path_capability.py tests/services/mcp/test_readonly_server.py`

## Acceptance checklist for D1

- `deeptutor.mcp.readonly_server` imports cleanly.
- MCP discovery lists exactly the nine v1 read-only tools.
- Every v1 tool returns the common JSON-safe envelope.
- No v1 MCP tool writes to sessions, KBs, settings, progress, or filesystem except unavoidable read-only runtime initialization.
- Missing Mastery Path reads return `not_found` without creating `<path_id>.json`.
- Hermes can discover the server with `mcp_servers.deeptutor` and see `mcp_deeptutor_*` tool names.
- Mastery Path routing deterministically targets `acumatica-courses-lightrag` when either `config.mastery_path_id` is set or that KB is the first selected `knowledge_bases` entry.
