# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — current-owned work MCP registration
"""Register the same explicit owned-work contract on stdio and HTTP MCP."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from synapse_channel.core.owned_work import MAX_OWNED_CHARS
from synapse_channel.mcp.bridge import SynapseHubBridge

Hex = Annotated[str, Field(strict=True, pattern=r"^(?:[0-9a-f]{64})?$")]
ReadMode = Annotated[
    Literal["list", "read"], Field(description="List own work keys or read one record chunk")
]
HubId = Annotated[
    str,
    Field(strict=True, max_length=256, description="Returned hub ID; empty only on initial list"),
]
AfterKey = Annotated[Hex, Field(description="Previous list cursor, empty for initial page")]
Revision = Annotated[
    Hex, Field(description="Previous list revision; restart listing when it changes")
]
PageLimit = Annotated[
    int, Field(strict=True, ge=1, le=100, description="Maximum scanned list keys")
]
RecordKey = Annotated[Hex, Field(description="Record key returned by list; read mode only")]
ItemVersion = Annotated[Hex, Field(description="Record mutation generation returned by list")]
Offset = Annotated[
    int,
    Field(
        strict=True,
        ge=0,
        le=9223372036854775807,
        description="Next accepted ASCII JSON position; read mode only",
    ),
]
Digest = Annotated[
    Hex, Field(description="Returned content SHA-256; required after the first chunk")
]
ChunkLimit = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=MAX_OWNED_CHARS,
        description="Maximum ASCII JSON characters in a record chunk",
    ),
]


def register_owned_work_tool(
    server: MCPServer, bridge: SynapseHubBridge, *, project: str | None
) -> None:
    """Fix the identity and optional HTTP project outside all model arguments."""

    @server.tool()
    async def synapse_owned_work(
        mode: ReadMode = "list",
        hub_id: HubId = "",
        after_key: AfterKey = "",
        revision: Revision = "",
        limit: PageLimit = 50,
        key: RecordKey = "",
        item_version: ItemVersion = "",
        offset: Offset = 0,
        digest: Digest = "",
        limit_chars: ChunkLimit = MAX_OWNED_CHARS,
    ) -> str:
        """Read current work assigned to this exact owner without the global board cap.

        List mode returns bounded opaque keys, generations and a scoped revision.
        Follow has_more, including empty pages, using hub_id/revision/after_key.
        Read mode requires hub_id/key/item_version; join content chunks in offset
        order before parsing JSON. Preserve content_sha256 as digest on continuation.
        Retry unchanged arguments after a lost reply; a changed generation, hub or
        digest refuses without committing progress. Reading grants no mutation
        authority and acknowledges neither work processing nor business effects.
        """
        return await bridge.owned_work(
            hub_id=hub_id,
            project=project,
            mode=mode,
            after_key=after_key,
            revision=revision,
            limit=limit,
            key=key,
            item_version=item_version,
            offset=offset,
            digest=digest,
            limit_chars=limit_chars,
        )
