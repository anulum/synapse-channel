# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a release receipt is what the release path writes, and nothing else
"""The release path's own notes are receipts; an ordinary assessment is not, so the
dashboard's receipts and the security posture count only what releases wrote."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipts import (
    RELEASE_RECEIPT_NOTE_KIND,
    RELEASE_RECEIPT_PREFIX,
    build_release_receipt,
    format_release_receipt_note,
    is_release_receipt_note,
)
from synapse_channel.dashboard import DashboardSnapshot, fetch_dashboard_snapshot
from synapse_channel.dashboard_security_posture import build_security_posture

RECEIPT_TEXT = format_release_receipt_note(
    build_release_receipt(task_id="T-1", owner="P/alice", evidence=["pytest -q"])
)


def _note(kind: str, text: str, task: str = "T-1") -> dict[str, Any]:
    return {"task_id": task, "author": "P/alice", "kind": kind, "text": text, "posted_at": 1.0}


def _receipts_row(document: dict[str, Any]) -> dict[str, Any]:
    rows = build_security_posture(document)["rows"]
    assert isinstance(rows, list)
    (row,) = [row for row in rows if row["surface"] == "receipts"]
    return dict(row)


def test_the_release_path_writes_its_marker_under_its_kind() -> None:
    assert RECEIPT_TEXT.startswith(f"{RELEASE_RECEIPT_PREFIX} evidence=pytest -q")
    assert is_release_receipt_note(_note(RELEASE_RECEIPT_NOTE_KIND, RECEIPT_TEXT))


def test_an_assessment_or_the_marker_alone_is_not_a_receipt() -> None:
    assert not is_release_receipt_note(_note("assessment", "looks good to me"))
    assert not is_release_receipt_note(_note("note", RECEIPT_TEXT))
    assert not is_release_receipt_note(_note("assessment", RECEIPT_TEXT.upper()))
    assert not is_release_receipt_note({"text": RECEIPT_TEXT})


def _document(progress: list[dict[str, Any]]) -> dict[str, Any]:
    return DashboardSnapshot(
        online_agents=[],
        state={"active_claims": [], "generated_at": 10.0},
        board={"tasks": [], "progress": progress},
        manifest=[],
    ).to_dict()


def test_a_mixed_board_keeps_only_the_receipts_and_the_posture_counts_them() -> None:
    mixed = _document(
        [
            _note("assessment", "review: fine", "T-0"),
            _note("assessment", RECEIPT_TEXT, "T-1"),
            _note("note", RECEIPT_TEXT, "T-2"),
            _note("finding", "a finding", "T-3"),
        ]
    )
    assessments_only = _document([_note("assessment", "review: fine", "T-0")])

    assert [receipt["task_id"] for receipt in mixed["fleet"]["receipts"]] == ["T-1"]
    assert _receipts_row(mixed)["detail"] == (
        "1 release receipt row(s) are visible in the board projection"
    )
    assert assessments_only["fleet"]["receipts"] == []
    assert _receipts_row(assessments_only)["state"] == "no-current-receipts"


async def test_a_real_release_with_evidence_is_the_one_receipt_on_the_dashboard() -> None:
    async with running_hub(SynapseHub(hub_id="release-receipts")) as (_, uri):
        alice = await connect_agent("P/alice", uri)
        await alice.agent.claim("T-1")
        await alice.agent.post_progress("T-1", "review: the change reads well", kind="assessment")
        await alice.agent.release("T-1", evidence=["pytest -q"])
        await alice.agent.claim("T-2")
        await alice.agent.release("T-2")  # no evidence: no receipt
        snapshot = await fetch_dashboard_snapshot(
            uri=uri, name="P/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )
        await close_agents(alice)

    document = snapshot.to_dict()
    progress = document["board"]["progress"]
    receipts = document["fleet"]["receipts"]
    assert sorted(note["text"] for note in progress if note["kind"] == "assessment") == sorted(
        ["review: the change reads well", receipts[0]["text"]]
    )
    assert [(row["task_id"], row["author"]) for row in receipts] == [("T-1", "P/alice")]
    assert receipts[0]["text"].startswith(f"{RELEASE_RECEIPT_PREFIX} evidence=pytest -q")
    assert _receipts_row(document)["state"] == "evidenced"


async def test_a_journalled_keyed_release_writes_the_receipt_through_its_draft(
    tmp_path: Path,
) -> None:
    """With a journal and an idempotency key the hub writes the receipt from the release draft."""
    journal = EventStore(tmp_path / "hub.db")
    async with running_hub(SynapseHub(hub_id="release-receipts-journal", journal=journal)) as (
        _,
        uri,
    ):
        alice = await connect_agent("P/alice", uri)
        await alice.agent.claim("T-1")
        await alice.agent.release("T-1", evidence=["pytest -q"], idem_key="release-T-1")
        snapshot = await fetch_dashboard_snapshot(
            uri=uri, name="P/observer", token=None, ready_timeout=2.0, response_timeout=2.0
        )
        await close_agents(alice)
    stored = [event.payload for event in journal.iter_events(kinds=("ledger_progress",))]
    journal.close()

    receipts = snapshot.to_dict()["fleet"]["receipts"]
    assert [row["task_id"] for row in receipts] == ["T-1"]
    assert [is_release_receipt_note(note) for note in stored] == [True]
