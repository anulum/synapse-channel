# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated HTTP MCP dispatch
"""Reuse registered MCP business actions behind project and resource limits."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from anyio import fail_after
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import ContentBlock, Resource, ResourceTemplate, Tool
from pydantic import AnyUrl, TypeAdapter

from synapse_channel.mcp.http_agent import HTTP_OPERATION_ID
from synapse_channel.mcp.http_arguments import HttpArgumentPolicy
from synapse_channel.mcp.http_config import MUTATION_TOOLS
from synapse_channel.mcp.http_policy import HttpOperationPolicy
from synapse_channel.mcp.http_views import ProjectMcpViews

_REPLY = TypeAdapter(object)
_MUTATION_REPLY = TypeAdapter(tuple[list[ContentBlock], dict[str, str]])
_MUTATION_SUCCESS = {
    "synapse_claim": "claim granted:",
    "synapse_release": "released '",
    "synapse_handoff": "handed off '",
    "synapse_task_declare": "declared '",
    "synapse_task_update": "updated '",
    "synapse_send": "sent to ",
}

_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RESOURCE_ACTIONS = {
    "synapse://board": "synapse_board",
    "synapse://state": "synapse_state",
    "synapse://manifest": "synapse_manifest",
    "synapse://directory": "synapse_directory",
}
_TEMPLATE_ACTIONS = {
    "synapse://task/{task_id}": "synapse_board",
    "synapse://agent/{agent}": "synapse_manifest",
    "synapse://resource-kind/{kind}": "synapse_state",
}


class HttpMcpServer(FastMCP[None]):
    """Apply remote authority while preserving the shared tool registration.

    Parameters
    ----------
    policy : HttpOperationPolicy
        Current issuer and operator admission for the bound seat.
    views : ProjectMcpViews
        Project-filtered public reads.
    operation_timeout : float
        Upper bound including waiting for the single bridge dispatcher.
    reply_bytes : int
        Maximum serialized content returned by an action or resource.
    **settings : Any
        Native SDK transport, authentication and session settings.

    Notes
    -----
    One dispatcher serializes bridge correlation. The application also bounds
    admitted HTTP requests, so waiting tasks cannot grow without a fixed limit.
    """

    def __init__(
        self,
        policy: HttpOperationPolicy,
        views: ProjectMcpViews,
        *,
        operation_timeout: float = 15.0,
        reply_bytes: int = 262144,
        **settings: Any,
    ) -> None:
        if not 0 < operation_timeout <= 60 or not 1024 <= reply_bytes <= 1048576:
            raise ValueError("invalid remote MCP operation limits")
        self.policy = policy
        self.views = views
        self.arguments = HttpArgumentPolicy(views.bridge, policy.project)
        self.operation_timeout = operation_timeout
        self.reply_bytes = reply_bytes
        self.dispatch = asyncio.Lock()
        super().__init__("synapse", **settings)

    def _bound(self, value: object) -> None:
        """Refuse an oversized reply before content is handed to the HTTP encoder."""
        if len(_REPLY.dump_json(value)) > self.reply_bytes:
            raise RuntimeError("remote MCP reply exceeds configured limit")

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Authorize and execute one registered tool with a stable mutation key."""
        token = None
        try:
            with fail_after(self.operation_timeout):
                async with self.dispatch:
                    await self.policy.authorize(name)
                    if name in MUTATION_TOOLS:
                        meta = self.get_context().request_context.meta
                        extras = meta.model_extra if meta is not None else None
                        operation = extras.get("synapse/operation-id") if extras else None
                        if (
                            not isinstance(operation, str)
                            or _OPERATION.fullmatch(operation) is None
                        ):
                            raise PermissionError("remote MCP mutation requires operation identity")
                        await self.arguments.authorize(name, arguments)
                        token = HTTP_OPERATION_ID.set(operation)
                    result = await super().call_tool(name, arguments)
                    if name in MUTATION_TOOLS:
                        _, structured = _MUTATION_REPLY.validate_python(result, strict=True)
                        expected = (
                            f"released '{arguments['task_id']}' with receipt "
                            f"owner '{self.policy.seat.seat}'"
                            if name == "synapse_release"
                            else _MUTATION_SUCCESS[name]
                        )
                        confirmed = (
                            structured["result"] == expected
                            if name == "synapse_release"
                            else structured["result"].startswith(expected)
                        )
                        if not confirmed:
                            raise RuntimeError("remote MCP mutation refused or unconfirmed")
                    self._bound(result)
                    return result
        except Exception:
            raise RuntimeError("remote MCP operation refused or unavailable") from None
        finally:
            if token is not None:
                HTTP_OPERATION_ID.reset(token)

    async def _allowed(self, action: str) -> bool:
        """Check live authority without exposing removed grants in discovery."""
        try:
            await self.policy.authorize(action)
        except PermissionError:
            return False
        return True

    async def list_tools(self) -> list[Tool]:
        """Advertise only tools currently permitted to this subject and session."""
        return [tool for tool in await super().list_tools() if await self._allowed(tool.name)]

    async def list_resources(self) -> list[Resource]:
        """Remove fixed resources whose corresponding read permission was revoked."""
        return [
            item
            for item in await super().list_resources()
            if await self._allowed(_RESOURCE_ACTIONS[str(item.uri)])
        ]

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """Advertise only scoped read templates with current read authority."""
        return [
            item
            for item in await super().list_resource_templates()
            if await self._allowed(_TEMPLATE_ACTIONS[item.uriTemplate])
        ]

    async def read_resource(self, uri: AnyUrl | str) -> Iterable[ReadResourceContents]:
        """Read project views directly so SDK diagnostics cannot log hub contents."""
        try:
            with fail_after(self.operation_timeout):
                async with self.dispatch:
                    value = str(uri)
                    action = _RESOURCE_ACTIONS.get(value)
                    if action is not None:
                        await self.policy.authorize(action)
                        reads = {
                            "synapse://board": self.views.board,
                            "synapse://state": self.views.state,
                            "synapse://manifest": self.views.manifest,
                            "synapse://directory": self.views.directory,
                        }
                        content = await reads[value]()
                    else:
                        content = await self._dynamic_resource(value)
                    self._bound(content)
                    return [ReadResourceContents(content=content, mime_type="text/plain")]
        except Exception:
            raise RuntimeError("remote MCP resource refused or unavailable") from None

    async def _dynamic_resource(self, value: str) -> str:
        """Validate a resource identifier and resolve it through scoped public views."""
        uri = urlsplit(value)
        if (
            uri.scheme != "synapse"
            or uri.query
            or uri.fragment
            or uri.username is not None
            or uri.password is not None
            or uri.port is not None
        ):
            raise PermissionError("remote MCP resource denied")
        identifier = unquote(uri.path.removeprefix("/"))
        if not identifier or len(identifier) > 256:
            raise PermissionError("remote MCP resource denied")
        if uri.netloc == "task":
            await self.policy.authorize("synapse_board")
            return await self.views.task_resource(identifier)
        if uri.netloc == "agent":
            await self.policy.authorize("synapse_manifest")
            return await self.views.agent_resource(identifier)
        if uri.netloc == "resource-kind":
            await self.policy.authorize("synapse_state")
            return await self.views.resource_kind_resource(identifier)
        raise PermissionError("remote MCP resource denied")
