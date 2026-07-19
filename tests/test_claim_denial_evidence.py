# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable content-minimized claim-denial evidence
"""Prove refused claims survive restart without retaining request content."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.handlers.leasing import apply_claim
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_claim_denial_is_durable_and_content_minimized_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "events.db"
    store = EventStore(database)
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    assert apply_claim(
        hub,
        "ALPHA",
        {
            "task_id": "private-task",
            "note": "prompt material must never persist",
            "worktree": "/secret/repository",
            "paths": ["private/source.py"],
        },
    ).ok

    denied = apply_claim(
        hub,
        "BETA",
        {
            "task_id": "blocked-task",
            "note": "attempted message body must never persist",
            "worktree": "/secret/repository",
            "paths": ["private/source.py"],
            "git": {"branch": "private-branch"},
        },
    )
    assert not denied.ok
    assert denied.reason_code == "SCOPE_CONFLICT"
    store.close()

    restarted = EventStore(database)
    events = [event for event in restarted.read_all() if event.kind == EventKind.CLAIM_DENIAL]
    restarted.close()

    assert len(events) == 1
    payload = events[0].payload
    assert payload == {
        "claimant": "BETA",
        "claimant_sha256": _digest("BETA"),
        "claimant_truncated": False,
        "decision": "deny",
        "path_count": 1,
        "reason_code": "SCOPE_CONFLICT",
        "scope_sha256": _digest(
            json.dumps(
                {"paths": ["private/source.py"], "worktree": "/secret/repository"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        "task_id_sha256": _digest("blocked-task"),
    }
    serialized = json.dumps(payload, sort_keys=True)
    for secret in (
        "blocked-task",
        "private/source.py",
        "/secret/repository",
        "attempted message body",
        "private-branch",
    ):
        assert secret not in serialized


def test_malformed_path_identity_denial_is_also_recorded(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    denied = apply_claim(
        hub,
        "BETA",
        {
            "task_id": "bad-identity",
            "paths": ["private/source.py"],
            "path_identity": {"worktree_path": 7},
        },
    )
    events = [event for event in store.read_all() if event.kind == EventKind.CLAIM_DENIAL]
    store.close()

    assert not denied.ok
    assert denied.reason_code == "PATH_IDENTITY_INVALID"
    assert len(events) == 1
    assert events[0].payload["reason_code"] == "PATH_IDENTITY_INVALID"
    assert "bad-identity" not in json.dumps(events[0].payload)


async def test_claim_denied_reply_matches_the_durable_reason_code(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    async with running_hub(hub) as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda message: message.get("type") == "claim_granted")
            await beta.agent.claim("T1")
            denied = await beta.recorder.wait_for(
                lambda message: message.get("type") == "claim_denied"
            )
        finally:
            await close_agents(alpha, beta)

    events = [event for event in store.read_all() if event.kind == EventKind.CLAIM_DENIAL]
    store.close()
    assert denied["reason_code"] == "LEASE_LIVE"
    assert events[-1].payload["reason_code"] == denied["reason_code"]
