"""Stdio MCP server exposing local DeepTutor state read-only."""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from deeptutor.mcp import readonly_tools as tools

ToolCallable = Callable[..., Awaitable[dict[str, Any]]]

_TOOL_REGISTRY: list[tuple[str, str, ToolCallable]] = [
    ("list_sessions", "List DeepTutor chat sessions without message bodies.", tools.list_sessions),
    ("get_session", "Fetch one DeepTutor session and capped messages.", tools.get_session),
    ("search_sessions", "Search DeepTutor session titles, summaries, and messages.", tools.search_sessions),
    ("get_turn_trace", "Fetch persisted DeepTutor streaming events for a turn.", tools.get_turn_trace),
    ("list_knowledge_bases", "List visible DeepTutor knowledge bases and status.", tools.list_knowledge_bases),
    ("search_kb", "Run a capped read-only retrieval query against a knowledge base.", tools.search_kb),
    ("list_mastery_paths", "List stored DeepTutor Mastery Path progress files.", tools.list_mastery_paths),
    ("get_mastery_path", "Fetch a persisted Mastery Path with safe redactions.", tools.get_mastery_path),
    ("get_mastery_map", "Fetch the next objective and dashboard map for a Mastery Path.", tools.get_mastery_map),
]


def create_server() -> FastMCP:
    """Create the read-only DeepTutor MCP server without starting transport."""
    mcp = FastMCP("DeepTutor read-only")
    for name, description, fn in _TOOL_REGISTRY:
        mcp.tool(name=name, description=description)(fn)
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m deeptutor.mcp.readonly_server",
        description="Run the local read-only DeepTutor MCP stdio server.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="Transport to run. v1 supports local stdio only.",
    )
    parser.parse_args(argv)
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()


__all__ = ["create_server", "main"]
