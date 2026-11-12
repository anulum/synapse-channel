# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — consuming and repeatable MCP inbox tools
"""Register distinct cursor-ownership contracts on the shared MCP surface."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from synapse_channel.mcp.bridge import SynapseHubBridge


def register_inbox_tools(server: MCPServer, bridge: SynapseHubBridge) -> None:
    """Register the legacy consuming inbox and an explicit caller-cursor reader.

    Parameters
    ----------
    server : MCPServer
        Existing MCP transport and tool catalogue.
    bridge : SynapseHubBridge
        Fixed native identity and admitted hub connection.
    """

    @server.tool()
    async def synapse_inbox(limit: int = 50) -> str:
        """Consume a bounded page of local durable messages for this bridge identity."""
        return await bridge.inbox(limit)

    @server.tool()
    async def synapse_inbox_page(
        hub_id: Annotated[
            str,
            Field(
                strict=True, max_length=256, description="Returned hub ID; empty on initial read"
            ),
        ] = "",
        after_seq: Annotated[
            int,
            Field(
                strict=True, ge=0, le=9223372036854775807, description="Last accepted page cursor"
            ),
        ] = 0,
        limit: Annotated[
            int,
            Field(
                strict=True, ge=1, le=100, description="Native page size before directed filtering"
            ),
        ] = 50,
    ) -> str:
        """Read exact directed messages without advancing a server cursor.

        Retry the same hub_id/after_seq after a lost response. Adopt the returned
        hub_id/cursor only after accepting the page. Follow has_more even when
        messages is empty. Reading does not acknowledge processing or business effects.
        """
        return await bridge.inbox_page(hub_id=hub_id, after_seq=after_seq, limit=limit)
