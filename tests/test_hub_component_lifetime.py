# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — configured checkpoint ownership across real serving lifetimes
"""Retain the factory's actual checkpoint when a serving hub is restarted."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.hub_component_lifetime import build_lifetime
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_factory import build_hub
from synapse_channel.core.merkle_checkpoint import (
    AntiRollbackError,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.persistence import EventStore

_UNCONFIGURED_REOPEN = Path(__file__).with_name("hub_component_lifetime_reopen.py")


@pytest.mark.parametrize("optimized", [False, True])
def test_unconfigured_checkpoint_refuses_before_io_with_native_optimization(
    tmp_path: Path, optimized: bool
) -> None:
    """The actual component API refuses consistently in ordinary and optimised Python."""
    with EventStore(tmp_path / "caller.db") as journal:
        config = HubConfig(journal=journal, anti_rollback_checkpoint=False)
        lifetime = build_lifetime(config, None, None)
        with pytest.raises(ValueError, match="requires a configured checkpoint path"):
            lifetime.reopen_checkpoint(journal)
        assert journal.count() == 0
    path = tmp_path / "events.db"
    process = subprocess.run(
        [
            sys.executable,
            *(["-O"] if optimized else []),
            "-W",
            "error",
            str(_UNCONFIGURED_REOPEN),
            str(path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 1, process.stderr
    assert process.stderr.rstrip().endswith(
        "ValueError: checkpoint reopening requires a configured checkpoint path"
    )
    assert "Exception ignored" not in process.stderr
    assert json.loads(process.stdout) == {"optimization": int(optimized)}
    assert not checkpoint_path_for(path).exists()
    with EventStore(path) as reopened:
        assert reopened.count() == 0


@pytest.mark.parametrize("tampering", ["truncate", "rewrite"])
async def test_restart_reverifies_the_original_configured_checkpoint(
    tmp_path: Path, tampering: Literal["truncate", "rewrite"]
) -> None:
    """A real clean stop retains its configured anchor across both journal attacks."""
    path = tmp_path / "events.db"
    checkpoint_path = tmp_path / "configured-chain.db"
    with EventStore(path) as journal:
        hub = build_hub(HubConfig(journal=journal, checkpoint_store_path=checkpoint_path))
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                await websocket.send(json.dumps({"type": "heartbeat", "sender": "P/writer"}))
                await read_until_type(websocket, "presence_update")
                await websocket.send(
                    json.dumps({"type": "chat", "sender": "P/writer", "payload": "original"})
                )
                assert (await read_until_type(websocket, "chat"))["payload"] == "original"
        written = journal.max_seq()
        assert written > 0
        with closing(MerkleCheckpointStore(checkpoint_path)) as checkpoints:
            original = checkpoints.latest()
            assert original is not None and original.seq == written
        with sqlite3.connect(path) as connection:
            if tampering == "truncate":
                connection.execute("DELETE FROM events WHERE seq = ?", (written,))
            else:
                row = connection.execute(
                    "SELECT payload FROM events WHERE seq = ?", (written,)
                ).fetchone()
                assert row is not None and "original" in row[0]
                changed = row[0].replace("original", "tampered")
                connection.execute(
                    "UPDATE events SET payload = ? WHERE seq = ?", (changed, written)
                )
        reason = "tail truncation" if tampering == "truncate" else "log rewrite"
        with pytest.raises(AntiRollbackError, match=reason):
            await asyncio.wait_for(hub.serve("127.0.0.1", 0), 5)
        assert journal.max_seq() == written - int(tampering == "truncate")
        with closing(MerkleCheckpointStore(checkpoint_path)) as checkpoints:
            assert checkpoints.latest() == original
        hub.close()
