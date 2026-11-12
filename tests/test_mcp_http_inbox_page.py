# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated HTTP inbox restart and cursor custody
"""Read durable exact-directed messages through genuine HTTPS MCP sessions."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, operation_meta, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app


async def test_directed_pages_repeat_across_native_and_http_restart(tmp_path: Path) -> None:
    """Two real subjects exchange a durable message; reading never commits a cursor."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    policy = json.loads(path.read_text())
    policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].append("synapse_inbox_page")
    path.write_text(json.dumps(policy))
    first: dict[str, Any] | None = None
    for incarnation in range(2):
        store = EventStore(tmp_path / "hub.db")
        hub = SynapseHub(
            hub_id="syn-inbox-http",
            journal=store,
            identity_trust_bundle=trust,
            require_identity_binding=True,
        )
        try:
            async with running_hub(hub) as (_, uri):
                app = build_http_mcp_app(
                    auth_file=path,
                    project="ALPHA",
                    hub_uri=uri,
                    allowed_hosts=["127.0.0.1:*"],
                    allowed_origins=[],
                    operation_timeout=5,
                )
                async with https_server(app, tmp_path) as (url, tls):
                    async with AsyncExitStack() as stack:
                        sessions: dict[str, ClientSession] = {}
                        for subject in ("alice", "bob"):
                            client = await stack.enter_async_context(
                                httpx2.AsyncClient(
                                    verify=tls,
                                    timeout=10,
                                    trust_env=False,
                                    headers={
                                        "Authorization": "Bearer "
                                        + access_token(
                                            issuer, sub=subject, scope="synapse:read synapse:mutate"
                                        )
                                    },
                                )
                            )
                            read, write = await stack.enter_async_context(
                                streamable_http_client(url + "/mcp", http_client=client)
                            )
                            session = await stack.enter_async_context(ClientSession(read, write))
                            await session.initialize()
                            sessions[subject] = session
                        alice, bob = sessions["alice"], sessions["bob"]
                        assert "synapse_inbox_page" not in {
                            tool.name for tool in (await bob.list_tools()).tools
                        }
                        assert (await bob.call_tool("synapse_inbox_page", {})).is_error
                        if not incarnation:
                            sent = await bob.call_tool(
                                "synapse_send",
                                {"target": "ALPHA/alice", "message": "durable directed message"},
                                meta=operation_meta("inbox-message"),
                            )
                            assert not sent.is_error
                        for _replay in range(2):
                            arguments = {} if first is None else {"hub_id": first["hub_id"]}
                            result = await alice.call_tool("synapse_inbox_page", arguments)
                            assert not result.is_error
                            assert result.structured_content is not None
                            page = json.loads(result.structured_content["result"])
                            assert [m["payload"] for m in page["messages"]] == [
                                "durable directed message"
                            ]
                            if first is None:
                                first = page
                            assert page == first
                        assert first is not None
                        resumed = await alice.call_tool(
                            "synapse_inbox_page",
                            {"hub_id": first["hub_id"], "after_seq": first["cursor"]},
                        )
                        assert not resumed.is_error and resumed.structured_content is not None
                        assert json.loads(resumed.structured_content["result"])["messages"] == []
                        for arguments in (
                            {"limit": True},
                            {"after_seq": "0"},
                            {"after_seq": 1},
                            {"hub_id": "another-hub"},
                            {"identity": "ALPHA/bob"},
                        ):
                            assert (await alice.call_tool("synapse_inbox_page", arguments)).is_error
                        if incarnation:
                            policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].remove(
                                "synapse_inbox_page"
                            )
                            path.write_text(json.dumps(policy))
                            assert "synapse_inbox_page" not in {
                                tool.name for tool in (await alice.list_tools()).tools
                            }
                            assert (await alice.call_tool("synapse_inbox_page", {})).is_error
        finally:
            store.close()
