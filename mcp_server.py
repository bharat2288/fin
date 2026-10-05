"""The MCP server at /mcp (fin-online D4).

Inside fin's one process, behind the chat gate: the access gate (outermost)
verifies the chat gate's token and serve.py's router sends `/mcp` here. The
server is the MCP SDK's (pinned, mcp 2.2.0) streamable-HTTP app, stateless with
JSON responses, folio's proven configuration.

The seam: the tools live in `mcp_tools.py` (fin-surfaces 01, another build).
Its interface is

    TOOLS                      a list of (name, description, input_schema, handler)
    call(name, args, actor)    -> dict; actor is the chat client the gate verified

This module only lists and dispatches them. Without `mcp_tools.py` the tool
list is empty and /mcp serves no tools. tests/test_mcp_inventory.py pins the
list. A tool failure reaches the chat as one fixed message, never exception
text.

As a second layer, like the Flask app's: a request that reaches this side
without the gate's chat identity is refused, so the server run without the
gate fails closed.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings

from access_gate import (
    ACCESS_DENIED_STATUS,
    ACCESS_IDENTITY_SCOPE_KEY,
    AUTH_REQUIRED_STATUS,
    CHAT_GATE,
    AccessIdentity,
    send_refusal,
)

logger = logging.getLogger("fin.mcp")

MCP_PATH = "/mcp"
SERVER_NAME = "fin"
UNKNOWN_TOOL = "unknown tool"
TOOL_FAILED = "the tool could not complete"

# The loopback hosts the SDK's DNS-rebinding guard admits in local-dev, where
# no gate stands in front (D1: local-dev only ever binds loopback).
_LOOPBACK_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
)
# Behind the gate the Host is fin's public name, and the gate's verified token
# is the lock; the SDK's localhost-only default would refuse it.
_GATED_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def load_registry() -> tuple[list[tuple], Callable[[str, dict, str], dict] | None]:
    """(mcp_tools.TOOLS, mcp_tools.call), or ([], None) when the module is absent."""
    try:
        import mcp_tools
    except ModuleNotFoundError as exc:
        if exc.name != "mcp_tools":
            raise
        return [], None
    return list(mcp_tools.TOOLS), mcp_tools.call


def chat_actor(request: Any) -> str | None:
    """The chat client the gate verified for this HTTP request, or None."""
    scope = getattr(request, "scope", None)
    identity = scope.get(ACCESS_IDENTITY_SCOPE_KEY) if isinstance(scope, dict) else None
    if isinstance(identity, AccessIdentity) and identity.gate == CHAT_GATE:
        return identity.actor
    return None


def build_server(tools: list[tuple], call: Callable[[str, dict, str], dict] | None) -> Server:
    listed = [
        types.Tool(name=name, description=description, input_schema=input_schema)
        for name, description, input_schema, _handler in tools
    ]
    names = {tool.name for tool in listed}

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        def failed(message: str) -> types.CallToolResult:
            return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)

        if call is None or params.name not in names:
            return failed(UNKNOWN_TOOL)
        actor = chat_actor(ctx.request)
        if actor is None:
            return failed(TOOL_FAILED)
        try:
            result = await anyio.to_thread.run_sync(call, params.name, dict(params.arguments or {}), actor)
        except Exception as exc:  # a fixed message; the type only is logged
            logger.error("mcp: tool %s failed - %s", params.name, type(exc).__name__)
            return failed(TOOL_FAILED)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, default=str))],
            structured_content=result,
        )

    return Server(SERVER_NAME, on_list_tools=list_tools, on_call_tool=call_tool)


class ChatEndpoint:
    """The ASGI app the router sends `/mcp` to."""

    def __init__(self, *, local_dev: bool, registry: tuple[list[tuple], Callable | None] | None = None):
        tools, call = registry if registry is not None else load_registry()
        self.server = build_server(tools, call)
        self._http = self.server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=_LOOPBACK_SECURITY if local_dev else _GATED_SECURITY,
        )

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """The SDK's session manager, run for the process's lifetime."""
        async with self.server.session_manager.run():
            yield

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            identity = scope.get(ACCESS_IDENTITY_SCOPE_KEY)
            if not isinstance(identity, AccessIdentity):
                await send_refusal(send, AUTH_REQUIRED_STATUS)
                return
            if identity.gate != CHAT_GATE:
                await send_refusal(send, ACCESS_DENIED_STATUS)
                return
        await self._http(scope, receive, send)
