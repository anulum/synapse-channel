# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — leasing drafts public runtime journeys

from __future__ import annotations

from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.git.path_identity import resolve_claim_scope_identity


@pytest.mark.parametrize(
    ("verb", "reply"),
    [
        ("task_update", "error"),
        ("release", "release_denied"),
        ("handoff", "handoff_denied"),
        ("checkpoint", "checkpoint_denied"),
    ],
)
async def test_keyed_stale_epoch_never_prepares_or_commits_a_grant(
    tmp_path: Path, verb: str, reply: str
) -> None:
    store = EventStore(tmp_path / "denial.db")
    hub = SynapseHub(journal=store, require_fencing_epoch=True)
    try:
        async with running_hub(hub) as (_, uri):
            owner = await connect_agent("DRAFT/owner", uri)
            recipient = await connect_agent("DRAFT/recipient", uri)
            try:
                await owner.agent.claim("lease", idem_key="acquire")
                grant = await owner.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
                before = hub.state.claims["lease"].as_persisted_dict()
                operations = store.read_operations()
                await owner.agent.send_message(
                    verb,
                    task_id="lease",
                    epoch=int(grant["epoch"]) + 1,
                    idem_key="stale-" + verb,
                    to_agent=recipient.agent.name,
                    checkpoint="resume",
                )
                denied = await owner.recorder.wait_for(lambda m: m.get("type") == reply)
                assert "epoch" in str(denied["payload"]).lower()
                assert hub.state.claims["lease"].as_persisted_dict() == before
                assert store.read_operations() == operations
            finally:
                await close_agents(owner, recipient)
    finally:
        store.close()


async def test_keyed_handoff_and_release_commit_their_progress_with_the_grant(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repository")
    root, paths, identity = resolve_claim_scope_identity(repo, ("file.txt",))
    store = EventStore(tmp_path / "progress.db")
    hub = SynapseHub(journal=store, require_fencing_epoch=True)
    try:
        async with running_hub(hub) as (_, uri):
            owner = await connect_agent("DRAFT/owner", uri)
            recipient = await connect_agent("DRAFT/recipient", uri)
            try:
                await owner.agent.claim(
                    "lease",
                    worktree=str(root),
                    paths=paths,
                    path_identity=identity.as_dict(),
                    idem_key="acquire",
                )
                granted = await owner.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
                start = len(recipient.recorder.messages)
                await owner.agent.handoff(
                    "lease",
                    recipient.agent.name,
                    note="continue from receipt",
                    epoch=int(granted["epoch"]),
                    idem_key="transfer",
                )
                handoff = await recipient.recorder.wait_for(
                    lambda m: m.get("type") == "handoff_granted"
                )
                assert handoff["path_identity"] == identity.as_dict()
                frames = recipient.recorder.messages[start:]
                assert [
                    m["type"]
                    for m in frames
                    if m.get("type") in {"ledger_progress_posted", "handoff_granted"}
                ] == ["ledger_progress_posted", "handoff_granted"]
                start = len(recipient.recorder.messages)
                await recipient.agent.release(
                    "lease",
                    epoch=int(handoff["epoch"]),
                    idem_key="finish",
                    evidence=["owning request trace"],
                    changed_files=["file.txt"],
                )
                release = await recipient.recorder.wait_for(
                    lambda m: m.get("type") == "release_granted"
                )
                await recipient.recorder.wait_for(
                    lambda m: (
                        m.get("type") == "ledger_progress_posted"
                        and m.get("note", {}).get("kind") == "assessment"
                    )
                )
                frames = recipient.recorder.messages[start:]
                assert [
                    m["type"]
                    for m in frames
                    if m.get("type") in {"ledger_progress_posted", "release_granted"}
                ] == ["release_granted", "ledger_progress_posted"]
                assert release["receipt"]["changed_files"] == ["file.txt"]
                assert "lease" not in hub.state.claims
                assert [row.response["type"] for row in store.read_operations()] == [
                    "claim_granted",
                    "handoff_granted",
                    "release_granted",
                ]
                kinds = [
                    e.kind
                    for e in store.read_all()
                    if e.kind
                    in {
                        EventKind.CLAIM,
                        EventKind.HANDOFF,
                        EventKind.RELEASE,
                        EventKind.LEDGER_PROGRESS,
                    }
                ]
                assert kinds == [
                    EventKind.CLAIM,
                    EventKind.HANDOFF,
                    EventKind.LEDGER_PROGRESS,
                    EventKind.RELEASE,
                    EventKind.LEDGER_PROGRESS,
                ]
            finally:
                await close_agents(owner, recipient)
    finally:
        store.close()
