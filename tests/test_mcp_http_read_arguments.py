# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — every remote read refuses an argument outside its own list
"""An argument the HTTP read does not declare never reaches the hub."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.mcp.http_application import build_http_mcp_app
from synapse_channel.mcp.http_arguments import _READ_ARGUMENTS, authorize_read_arguments
from synapse_channel.mcp.http_config import OPTIONAL_READ_TOOLS, READ_TOOLS

_REFUSAL = "remote MCP operation refused or unavailable"
_DENIED = "remote MCP read arguments denied"
_OUTSIDE = {"identity": "ALPHA/eve"}


def test_every_remote_read_has_its_own_argument_list() -> None:
    """The check covers the five defaults and both grant-only reads, and no other tool."""
    assert set(_READ_ARGUMENTS) == set(READ_TOOLS | OPTIONAL_READ_TOOLS)
    assert _READ_ARGUMENTS["synapse_board"] == frozenset()
    assert _READ_ARGUMENTS["synapse_inbox_page"] == frozenset({"hub_id", "after_seq", "limit"})
    authorize_read_arguments("synapse_claim", {"paths": ["src"]})
    authorize_read_arguments("synapse_inbox_page", {"hub_id": "syn", "after_seq": 0, "limit": 1})
    authorize_read_arguments("synapse_board", {})
    with pytest.raises(PermissionError, match=f"^{_DENIED}$"):
        authorize_read_arguments("synapse_status", dict(_OUTSIDE))


@pytest.mark.parametrize("tool", sorted(READ_TOOLS | OPTIONAL_READ_TOOLS))
async def test_https_read_refuses_an_argument_outside_its_own_list(
    tool: str, tmp_path: Path
) -> None:
    """Each remote read, over real HTTPS, returns the whole fixed refusal and nothing else."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    provision(grants, issuer)
    policy = json.loads(grants.read_text(encoding="utf-8"))
    policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].extend(sorted(OPTIONAL_READ_TOOLS))
    grants.write_text(json.dumps(policy), encoding="utf-8")
    async with running_hub() as (_, uri):
        app = build_http_mcp_app(
            auth_file=grants,
            project="ALPHA",
            hub_uri=uri,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
            operation_timeout=5,
        )
        async with https_server(app, tmp_path) as (url, tls):
            async with httpx2.AsyncClient(
                verify=tls,
                trust_env=False,
                timeout=10,
                headers={"Authorization": "Bearer " + access_token(issuer, scope="synapse:read")},
            ) as client:
                async with streamable_http_client(url + "/mcp", http_client=client) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        refused = await session.call_tool(tool, dict(_OUTSIDE))
                        assert refused.is_error
                        visible = [
                            item.text for item in refused.content if isinstance(item, TextContent)
                        ]
                        assert visible == [_REFUSAL]
                        if tool in READ_TOOLS:
                            admitted = await session.call_tool(tool, {})
                            assert admitted.is_error is False
