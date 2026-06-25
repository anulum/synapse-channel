# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.state import GitContext

# --- scoped claims + epoch ---------------------------------------------------


async def test_claim_broadcasts_scope_and_epoch() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="A", type="claim", task_id="T1", worktree="wt", paths=["src"]), ws
    )
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["worktree"] == "wt"
    assert granted["paths"] == ["src"]
    assert granted["epoch"] == 1


async def test_claim_carries_git_context() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    git = {"branch": "feature/x", "base": "main", "auto_release_on": "merge"}
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", git=git), ws)
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["git"] == git
    assert hub.state.claims["T1"].git == GitContext(
        branch="feature/x", base="main", auto_release_on="merge"
    )


async def test_claim_without_git_leaves_it_unset() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["git"] is None
    assert hub.state.claims["T1"].git is None


async def test_scoped_claim_overlap_is_denied() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(
        _msg(sender="B", type="claim", task_id="T2", paths=["src/app.py"]), ws_b
    )
    assert ws_b.last()["type"] == "claim_denied"
    assert "file scope conflicts with 'T1'" in ws_b.last()["payload"]


async def test_release_with_matching_epoch_is_granted() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    epoch = hub.state.claims["T1"].epoch
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1", epoch=epoch), ws)
    assert any(m.get("type") == "release_granted" for m in ws.decoded())


async def test_release_with_stale_epoch_is_denied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1", epoch=999), ws)
    assert ws.last()["type"] == "release_denied"
    assert "epoch is stale" in ws.last()["payload"]
    assert "T1" in hub.state.claims


async def test_task_update_with_stale_epoch_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="done", epoch=999), ws
    )
    assert ws.last()["type"] == "error"
    assert "epoch is stale" in ws.last()["payload"]


def test_optional_int_parsing() -> None:
    assert SynapseHub._optional_int({"epoch": 5}, "epoch") == 5
    assert SynapseHub._optional_int({"epoch": 7.0}, "epoch") == 7
    assert SynapseHub._optional_int({"epoch": True}, "epoch") is None
    assert SynapseHub._optional_int({"epoch": "x"}, "epoch") is None
    assert SynapseHub._optional_int({}, "epoch") is None
