# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-APPROVAL: forged and replayed approval notes on a real hub
"""Approvals stay advisory; what they record is who the hub bound, not what a frame claims.

K4-APPROVAL (owner-accepted T4-1): approval notes remain advisory evidence in 0.99.x.
These tests pin what that evidence is worth against forgery and replay, through a
real hub and its durable log:

* a frame cannot name its own author — the hub records the bound connection name,
  and a free-text reason naming someone else changes nothing;
* a frame cannot switch to another seat's name on a bound socket (``4009``);
* resending the exact frame is idempotent, while a fresh request with the same
  content is a second, attributed event that leaves the decision unchanged;
* every report keeps its advisory label, so none of this reads as a runtime gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from hub_e2e_helpers import collect_available, running_hub, send_json
from synapse_channel.core.approvals import (
    approvals_to_json,
    build_approval_report,
    format_approval_note,
    render_human,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_NOTE = format_approval_note(
    subject="RELEASE-7", state="approved", reason="signed off by P/reviewer"
)


def _approval_frame(sender: str, idem_key: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": MessageType.LEDGER_PROGRESS,
        "sender": sender,
        "target": "System",
        "task_id": "RELEASE-7",
        "kind": "approval",
        "text": _NOTE,
        "idem_key": idem_key,
        **extra,
    }


async def _bind(websocket: Any, name: str) -> None:
    await send_json(websocket, type="heartbeat", sender=name, target="System", payload="online")
    await collect_available(websocket, 0.3)


async def test_forged_author_and_replay_are_attributed_to_the_bound_sender(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            async with connect(uri) as websocket:
                await _bind(websocket, "P/mallory")
                forged = _approval_frame("P/mallory", "k1", author="P/reviewer")
                await send_json(websocket, **forged)
                first = await collect_available(websocket, 0.3)
                await send_json(websocket, **forged)
                replayed = await collect_available(websocket, 0.3)
                await send_json(websocket, **_approval_frame("P/mallory", "k2"))
                resent = await collect_available(websocket, 0.3)
        report = build_approval_report(tuple(store.read_all()))
    finally:
        store.close()

    posted = MessageType.LEDGER_PROGRESS_POSTED
    assert [message["type"] for message in first] == [posted]
    assert first[0]["note"]["author"] == "P/mallory"
    # The exact frame again is answered from the idempotency record: no new event.
    assert [message["type"] for message in replayed] == [posted]
    assert [message["type"] for message in resent] == [posted]

    status = report.by_subject["RELEASE-7"]
    assert (status.current_state, status.decided_by) == ("approved", "P/mallory")
    assert [(event.actor, event.state) for event in status.history] == [
        ("P/mallory", "approved"),
        ("P/mallory", "approved"),
    ]
    # The reason is free text: it is shown, never used as attribution.
    assert status.decision_reason == "signed off by P/reviewer"
    assert approvals_to_json(report)["note"] == (
        "advisory approval evidence and audit trail, not a runtime gate"
    )
    assert render_human(report).startswith("Approval gates: advisory evidence, not a runtime gate")


async def test_a_bound_socket_cannot_decide_under_another_seat_name(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            async with connect(uri) as websocket:
                await _bind(websocket, "P/mallory")
                await send_json(websocket, **_approval_frame("P/reviewer", "k1"))
                with pytest.raises(ConnectionClosedError) as closed:
                    await collect_available(websocket, 0.5)
        report = build_approval_report(tuple(store.read_all()))
    finally:
        store.close()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 4009
    assert "RELEASE-7" not in report.by_subject
