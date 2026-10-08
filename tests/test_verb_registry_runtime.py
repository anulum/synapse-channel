# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub admission through handler-owned verb declarations
"""Exercise ACL targets against durable leases over actual WebSocket connections."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.acl import CLAIM, RELEASE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

_SENDER = "P/registry-recorder"


@pytest.fixture
async def seeded_store(tmp_path: Path) -> AsyncIterator[EventStore]:
    """Create leases through the real wire, retaining them for a secured restart.

    The original hub accepts both leases. The new policy grants mutations only
    on one, modeling a policy tightened while an existing owner retains leases.
    No state injection, mocked gate or SQL fabrication creates the test lease.
    """
    store = EventStore(tmp_path / "registry.db")
    try:
        async with running_hub(SynapseHub(journal=store, hub_id="registry-test")) as (_, uri):
            async with connect(uri) as websocket:
                await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
                for task_id in ("allowed", "restricted"):
                    await send_json(
                        websocket,
                        sender=_SENDER,
                        type="claim",
                        task_id=task_id,
                        note="original",
                    )
                    grant = await read_until_type(websocket, "claim_granted")
                    assert grant["task_id"] == task_id
        yield store
    finally:
        store.close()


def _enforcing_hub(store: EventStore) -> SynapseHub:
    """Restore the recorded leases with a policy granting only the allowed task."""
    policy = AclPolicy(
        [
            AclRule(CLAIM, "claim", "allowed", "P"),
            AclRule(CLAIM, "path", "src/*", "P"),
            AclRule(RELEASE, "claim", "allowed", "P"),
        ]
    )
    return SynapseHub(journal=store, hub_id="registry-test", acl_policy=policy, require_acl=True)


@pytest.mark.parametrize(
    "selectors",
    [
        {"id": "restricted", "payload": "allowed"},
        {"task_id": "", "id": "restricted", "payload": "allowed"},
        {"task_id": "restricted", "id": "allowed", "payload": "allowed"},
    ],
)
async def test_task_update_authorizes_the_actual_task_before_mutation(
    seeded_store: EventStore, selectors: dict[str, str]
) -> None:
    """Neither the payload nor an ignored alias can smuggle an unauthorized target."""
    hub = _enforcing_hub(seeded_store)
    original_events = seeded_store.max_seq()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(
                websocket,
                sender=_SENDER,
                type="task_update",
                note="unauthorized",
                **selectors,
            )
            denied = await read_until_type(websocket, "error")
            assert denied["acl_decision"] == "would_deny"
            assert hub.state.claims["restricted"].note == "original"
    assert not [
        event for event in seeded_store.read_since(original_events) if event.kind == "task_update"
    ]


async def test_task_update_id_alias_is_allowed_for_its_actual_target(
    seeded_store: EventStore,
) -> None:
    """An irrelevant payload cannot block an authorized id-based update."""
    hub = _enforcing_hub(seeded_store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(
                websocket,
                sender=_SENDER,
                type="task_update",
                id="allowed",
                payload="restricted",
                note="authorized",
            )
            updated = await read_until_type(websocket, "task_updated")
            assert updated["task_id"] == "allowed"
            assert hub.state.claims["allowed"].note == "authorized"
            assert hub.state.claims["restricted"].note == "original"
    assert [
        event.payload["task_id"] for event in seeded_store.read_all() if event.kind == "task_update"
    ] == ["allowed"]


@pytest.mark.parametrize(
    "selectors",
    [{"payload": "allowed"}, {"task_id": "allowed", "payload": "restricted"}],
)
async def test_release_resolves_payload_fallback_and_task_id_precedence(
    seeded_store: EventStore, selectors: dict[str, str]
) -> None:
    """The ACL grant applies to the exact lease the wire handler releases."""
    hub = _enforcing_hub(seeded_store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(websocket, sender=_SENDER, type="release", **selectors)
            released = await read_until_type(websocket, "release_granted")
            assert released["task_id"] == "allowed"
            assert "allowed" not in hub.state.claims
            assert "restricted" in hub.state.claims
    assert [
        event.payload["task_id"] for event in seeded_store.read_all() if event.kind == "release"
    ] == ["allowed"]


async def test_release_payload_cannot_release_an_ungranted_lease(
    seeded_store: EventStore,
) -> None:
    """The denied payload target remains owned and produces no release event."""
    hub = _enforcing_hub(seeded_store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(websocket, sender=_SENDER, type="release", payload="restricted")
            denied = await read_until_type(websocket, "error")
            assert denied["acl_decision"] == "would_deny"
            assert "restricted" in hub.state.claims
    assert not [event for event in seeded_store.read_all() if event.kind == "release"]


async def test_claim_payload_still_authorizes_normalized_paths(
    seeded_store: EventStore,
) -> None:
    """Preserve the original payload alias and normalized path admission together."""
    hub = _enforcing_hub(seeded_store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(
                websocket,
                sender=_SENDER,
                type="claim",
                payload="allowed",
                paths=["src//allowed.py"],
            )
            granted = await read_until_type(websocket, "claim_granted")
            assert granted["task_id"] == "allowed"
            assert granted["paths"] == ["src/allowed.py"]


async def test_traversal_path_requires_root_scope_before_claim_renewal(
    seeded_store: EventStore,
) -> None:
    """A traversal-like declaration cannot borrow a narrower src/* path grant."""
    hub = _enforcing_hub(seeded_store)
    original_seq = seeded_store.max_seq()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(
                websocket,
                sender=_SENDER,
                type="claim",
                payload="allowed",
                paths=["src/module/../allowed.py"],
            )
            denied = await read_until_type(websocket, "error")
            assert denied["acl_decision"] == "would_deny"
            assert hub.state.claims["allowed"].paths == ()
    assert not [event for event in seeded_store.read_since(original_seq) if event.kind == "claim"]


@pytest.mark.parametrize("query", [None, [], {"identity": 42}])
async def test_malformed_mailbox_query_cannot_bypass_recall_admission(
    seeded_store: EventStore, query: object
) -> None:
    """An invalid query cannot fall through to an ungated global history read."""
    hub = _enforcing_hub(seeded_store)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await send_json(websocket, sender=_SENDER, type="heartbeat", payload="ready")
            await send_json(
                websocket,
                sender=_SENDER,
                type="history_request",
                inbox_query=query,
            )
            denied = await read_until_type(websocket, "error")
            assert denied["acl_decision"] == "would_deny"
