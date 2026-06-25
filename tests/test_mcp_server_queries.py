# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import json

from mcp_server_helpers import agent_of, drive, make_bridge
from synapse_channel.core.protocol import MessageType


async def test_board_returns_json() -> None:
    bridge = make_bridge()
    board = {"tasks": [{"task_id": "T1"}], "ready": []}
    reply = {"type": MessageType.BOARD_SNAPSHOT, "board": board}
    out = await drive(bridge, bridge.board, reply)
    assert json.loads(out) == board
    assert ("request_board",) in agent_of(bridge).calls


async def test_board_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.board()
    assert "did not return the board" in out


async def test_state_returns_json() -> None:
    bridge = make_bridge()
    snapshot = {"active_claims": [{"task_id": "T1"}]}
    reply = {"type": MessageType.STATE_SNAPSHOT, "snapshot": snapshot}
    out = await drive(bridge, bridge.state, reply)
    assert json.loads(out) == snapshot


async def test_state_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.state()
    assert "did not return its state" in out


async def test_manifest_returns_json() -> None:
    bridge = make_bridge()
    manifest = [{"agent": "ALPHA", "task_classes": ["chat"]}]
    reply = {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": manifest}
    out = await drive(bridge, bridge.manifest, reply)
    assert json.loads(out) == manifest


async def test_manifest_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.manifest()
    assert "did not return the manifest" in out
