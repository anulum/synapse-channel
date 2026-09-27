# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTTP task reference authority
"""Verify real foreign declarations cannot acquire authority through a task prefix."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import Recorder, running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_keys import write_signing_key
from synapse_channel.core.message_auth import EventSignatureKey, EventSignatureTrustBundle
from synapse_channel.mcp.http_application import build_http_mcp_app


async def test_native_foreign_creator_cannot_import_task_authority(tmp_path: Path) -> None:
    """A genuine signed foreign seat can publish data but cannot grant HTTP task access."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    foreign_key = Ed25519PrivateKey.generate()
    foreign_key_file = tmp_path / "foreign.pem"
    write_signing_key(foreign_key_file, foreign_key)
    keys = dict(trust.keys)
    keys["foreign-1"] = EventSignatureKey.from_private_key(
        key_id="foreign-1", private_key=foreign_key, senders=frozenset({"BETA/worker"})
    )
    trust = EventSignatureTrustBundle(keys=keys, replay_cache=trust.replay_cache)
    hub = SynapseHub(identity_trust_bundle=trust, require_identity_binding=True)
    async with running_hub(hub) as (_, uri):
        recorder = Recorder()
        agent = SynapseAgent(
            "BETA/worker",
            recorder,
            uri=uri,
            verbose=False,
            identity_key_path=str(foreign_key_file),
            identity_key_id="foreign-1",
            machine_identity=False,
        )
        connection = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(3)
            await agent.post_task("ALPHA/collision", "FOREIGN_PRIVATE", project="ALPHA")
            await recorder.wait_for(lambda frame: frame.get("type") == "ledger_task_posted")
            assert hub.blackboard.tasks["ALPHA/collision"].created_by == "BETA/worker"
            app = build_http_mcp_app(
                auth_file=grants,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
            )
            async with https_server(app, tmp_path) as (url, tls):
                async with httpx.AsyncClient(
                    verify=tls,
                    trust_env=False,
                    timeout=5,
                    headers={
                        "Authorization": "Bearer "
                        + access_token(issuer, scope="synapse:read synapse:mutate")
                    },
                ) as client:
                    async with streamable_http_client(url + "/mcp", http_client=client) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            for tool, arguments in (
                                ("synapse_claim", {"task_id": "ALPHA/collision"}),
                                (
                                    "synapse_task_declare",
                                    {"task_id": "ALPHA/collision", "title": "overwrite"},
                                ),
                                (
                                    "synapse_task_declare",
                                    {
                                        "task_id": "ALPHA/dependency",
                                        "title": "dependent",
                                        "depends_on": ["ALPHA/collision"],
                                    },
                                ),
                            ):
                                denied = await session.call_tool(
                                    tool,
                                    arguments,
                                    meta={"synapse/operation-id": "foreign-collision"},
                                )
                                assert denied.isError
                                assert "FOREIGN_PRIVATE" not in denied.model_dump_json()
                            board = await session.call_tool("synapse_board")
                            assert not board.isError
                            assert "FOREIGN_PRIVATE" not in board.model_dump_json()
                            assert not hub.state.claims
                            assert (
                                hub.blackboard.tasks["ALPHA/collision"].title == "FOREIGN_PRIVATE"
                            )
                            assert "ALPHA/dependency" not in hub.blackboard.tasks
        finally:
            agent.running = False
            connection.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connection
