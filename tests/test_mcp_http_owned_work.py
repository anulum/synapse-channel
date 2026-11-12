# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact native pending work network contracts
"""Retrieve actual task and ad-hoc claim bodies under a global board cap of one."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app
from test_pending_index import claim, task


async def page(session: ClientSession, **arguments: object) -> dict[str, Any]:
    result = await session.call_tool("synapse_owned_work", arguments)
    assert not result.is_error and result.structured_content is not None
    value: dict[str, Any] = json.loads(result.structured_content["result"])
    return value


async def test_https_owned_reader_closes_capped_board_gap_without_expanding_grants(
    tmp_path: Path,
) -> None:
    issuer = Ed25519PrivateKey.generate()
    policy_path = tmp_path / "grants.json"
    trust = provision(policy_path, issuer)
    policy = json.loads(policy_path.read_text())
    policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].append("synapse_owned_work")
    policy_path.write_text(json.dumps(policy))
    store = EventStore(tmp_path / "hub.db")
    description = "Priradená úloha 🌍 " * 3000
    for value in (
        task(suggested_owner="ALPHA/alice", description=description),
        task("ALPHA/current", suggested_owner="ALPHA/alice", created_by="ALPHA/alice"),
        task("bare", project="", suggested_owner="ALPHA/alice"),
        task("BETA/assigned", project="BETA", suggested_owner="ALPHA/alice"),
        task("BETA/newest", project="BETA", suggested_owner="BETA/worker", updated_at=time.time()),
    ):
        store.append(EventKind.LEDGER_TASK, value.as_dict())
    store.append(
        EventKind.HANDOFF,
        claim(
            owner="ALPHA/alice",
            checkpoint=description,
            claimed_at=time.time(),
            lease_expires_at=time.time() + 600,
        ).as_persisted_dict(),
    )
    hub = SynapseHub(
        hub_id="owned-https",
        journal=store,
        board_task_cap=1,
        identity_trust_bundle=trust,
        require_identity_binding=True,
        anti_rollback_checkpoint=False,
    )
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=policy_path,
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
                                    "Authorization": "Bearer " + access_token(issuer, sub=subject)
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
                    assert "synapse_owned_work" in {
                        t.name for t in (await alice.list_tools()).tools
                    }
                    assert "synapse_owned_work" not in {
                        t.name for t in (await bob.list_tools()).tools
                    }
                    assert (await bob.call_tool("synapse_owned_work", {})).is_error
                    for name, key in (
                        ("synapse_board", "tasks"),
                        ("synapse_state", "active_claims"),
                    ):
                        result = await alice.call_tool(name, {})
                        assert result.structured_content is not None
                        assert json.loads(result.structured_content["result"])[key] == []
                    arguments: dict[str, object] = {"limit": 1}
                    items: list[dict[str, Any]] = []
                    while True:
                        current = await page(alice, **arguments)
                        assert await page(alice, **arguments) == current
                        assert (
                            current["project"] == "ALPHA" and current["identity"] == "ALPHA/alice"
                        )
                        items.extend(current["items"])
                        if not current["has_more"]:
                            break
                        arguments |= {
                            "hub_id": current["hub_id"],
                            "after_key": current["cursor"],
                            "revision": current["revision"],
                        }
                    assert len(items) == 3
                    records: dict[str, dict[str, Any]] = {}
                    for item in items:
                        arguments = {
                            "mode": "read",
                            "hub_id": current["hub_id"],
                            "key": item["key"],
                            "item_version": item["item_version"],
                        }
                        chunks: list[str] = []
                        while True:
                            chunk = await page(alice, **arguments)
                            assert await page(alice, **arguments) == chunk
                            chunks.append(chunk["content"])
                            if not chunk["has_more"]:
                                break
                            arguments |= {
                                "offset": chunk["next_offset"],
                                "digest": chunk["content_sha256"],
                            }
                        content = "".join(chunks)
                        assert (
                            hashlib.sha256(content.encode("ascii")).hexdigest()
                            == chunk["content_sha256"]
                        )
                        record = json.loads(content)
                        records[record["task_id"]] = record
                    assert set(records) == {"ALPHA/old", "ALPHA/current", "ALPHA/handoff"}
                    assert records["ALPHA/old"]["description"] == description
                    assert records["ALPHA/handoff"]["checkpoint"] == description
                    assert (
                        await alice.call_tool(
                            "synapse_claim",
                            {
                                "task_id": "ALPHA/old",
                                "task_only": True,
                                "operation_id": "read-does-not-authorize-write",
                            },
                        )
                    ).is_error
                    policy["subjects"]["bob"]["projects"]["ALPHA"]["tools"].append(
                        "synapse_owned_work"
                    )
                    policy_path.write_text(json.dumps(policy))
                    assert "synapse_owned_work" not in {
                        t.name for t in (await bob.list_tools()).tools
                    }
                    assert (await bob.call_tool("synapse_owned_work", {})).is_error
                    for invalid in (
                        {"identity": "ALPHA/bob"},
                        {"project": "BETA"},
                        {"limit": True},
                        {"offset": "0"},
                        {"after_key": "a" * 64},
                        {"mode": "exec"},
                    ):
                        assert (await alice.call_tool("synapse_owned_work", invalid)).is_error
                    policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].remove(
                        "synapse_owned_work"
                    )
                    policy_path.write_text(json.dumps(policy))
                    assert "synapse_owned_work" not in {
                        t.name for t in (await alice.list_tools()).tools
                    }
                    assert (await alice.call_tool("synapse_owned_work", {})).is_error
    finally:
        store.close()
