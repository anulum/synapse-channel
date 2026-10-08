# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real parsed CLI startup resource ownership
"""Exercise rejected starts and terminal outcomes against real SQLite stores."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Coroutine
from pathlib import Path

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.persistence import EventStore


class RecordedStore(EventStore):
    """Real event store recording successful ownership release."""

    closes = 0

    def close(self) -> None:
        """Close SQLite and count each attempted release."""
        self.closes += 1
        super().close()


@pytest.mark.parametrize(
    "flags",
    [
        ["--message-auth-key", "invalid"],
        ["--acl-policy", "absent-acl.json"],
        ["--require-identity-binding"],
        ["--capability-card-history-db", "absent-history.db"],
        ["--federation-observe-only"],
        ["--federation-offer", "absent-offer.json"],
        ["--namespace-owner", "PROJECT=remote"],
        ["--multihub-watch", "peer=ws://127.0.0.1:1"],
        ["--claim-peer", "peer=ws://127.0.0.1:1"],
        ["--relay-peer-pin", "peer=sha256:invalid"],
        ["--message-peer-pin", "peer=sha256:invalid"],
        ["--claim-peer-pin", "peer=sha256:invalid"],
    ],
)
def test_refused_configuration_closes_the_real_opened_journal_once(
    tmp_path: Path, flags: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Every post-allocation refusal releases its actual SQLite connection."""
    stores: list[RecordedStore] = []

    def open_store(path: str, *, key_file: str | None = None) -> RecordedStore:
        """Capture the real command allocation without replacing storage."""
        store = RecordedStore(path, key_file=key_file)
        stores.append(store)
        return store

    args = build_parser().parse_args(["hub", "--db", str(tmp_path / "events.db"), *flags])
    assert _cmd_hub(args, store_factory=open_store) == 2
    assert capsys.readouterr().err
    assert len(stores) == 1
    assert stores[0].closes == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        stores[0].count()


@pytest.mark.parametrize("outcome", ["normal", "interrupt", "serve-error", "init-error"])
def test_terminal_command_outcome_releases_journal_once(tmp_path: Path, outcome: str) -> None:
    """One owner covers normal, interrupted and exceptional command exits."""
    store = RecordedStore(tmp_path / "events.db")

    def open_store(path: str, *, key_file: str | None = None) -> RecordedStore:
        """Return the real store whose lifetime is observed after command exit."""
        assert Path(path) == tmp_path / "events.db"
        assert key_file is None
        return store

    def runner(coroutine: Coroutine[object, object, None]) -> None:
        """Close the unstarted coroutine and expose a terminal runner outcome."""
        coroutine.close()
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "serve-error":
            raise RuntimeError("observed serve failure")

    def rejected_hub(**kwargs: object) -> SynapseHub:
        """Fail initialization after the command acquired a real store."""
        assert kwargs["journal"] is store
        raise OSError("observed initialization failure")

    args = build_parser().parse_args(["hub", "--db", str(tmp_path / "events.db")])
    if outcome == "init-error":
        assert _cmd_hub(args, store_factory=open_store, hub_factory=rejected_hub) == 2
    elif outcome == "serve-error":
        with pytest.raises(RuntimeError, match="observed serve failure"):
            _cmd_hub(args, store_factory=open_store, runner=runner)
    else:
        assert _cmd_hub(args, store_factory=open_store, runner=runner) == 0
    assert store.closes == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.count()


def test_late_attachment_refusal_closes_real_replay_store_before_journal(
    tmp_path: Path,
) -> None:
    """Nested allocations unwind in reverse order without duplicate closes."""
    closed: list[str] = []

    class Journal(EventStore):
        """Real event store recording its order of release."""

        def close(self) -> None:
            """Release SQLite and observe journal ownership ending."""
            closed.append("journal")
            super().close()

    class Replay(DurableMessageAuthReplayStore):
        """Real replay store recording its order of release."""

        def close(self) -> None:
            """Release SQLite and observe replay ownership ending."""
            closed.append("replay")
            super().close()

    args = build_parser().parse_args(
        [
            "hub",
            "--db",
            str(tmp_path / "events.db"),
            "--require-message-auth",
            "--message-auth-key",
            "key:test-key:agent",
            "--attachment-recipient-policy",
            str(tmp_path / "missing-policy.json"),
        ]
    )
    assert _cmd_hub(args, store_factory=Journal, replay_store_factory=Replay) == 2
    assert closed == ["replay", "journal"]


def test_unstarted_hub_closes_checkpoint_before_journal_and_anchors_last_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A constructed but unserved hub still releases and anchors its own store."""
    from synapse_channel.core import hub as hub_module

    checkpoints: list[MerkleCheckpointStore] = []
    database = tmp_path / "events.db"

    class Checkpoint(MerkleCheckpointStore):
        """Real checkpoint database retained for a post-command ownership probe."""

        def __init__(self, path: Path) -> None:
            """Open a real checkpoint and expose it to the lifetime observation."""
            super().__init__(path)
            checkpoints.append(self)

    monkeypatch.setattr(hub_module, "MerkleCheckpointStore", Checkpoint)

    def build_hub(**kwargs: object) -> SynapseHub:
        """Create the real hub, then commit a final event before it is abandoned."""
        hub = SynapseHub.from_config(HubConfig.from_kwargs(kwargs))
        journal = kwargs["journal"]
        assert isinstance(journal, EventStore)
        journal.append(
            EventKind.CHAT,
            {"sender": "agent", "target": "all", "type": "chat", "payload": "proof"},
            durable=True,
        )
        return hub

    def runner(coroutine: Coroutine[object, object, None]) -> None:
        """Abandon the unstarted server without abandoning its checkpoint."""
        coroutine.close()

    args = build_parser().parse_args(["hub", "--db", str(database)])
    assert _cmd_hub(args, hub_factory=build_hub, runner=runner) == 0
    assert len(checkpoints) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        checkpoints[0].latest()
    checkpoint_path = Path(str(database) + ".checkpoint.db")
    with EventStore(database) as journal:
        reopened = MerkleCheckpointStore(checkpoint_path)
        try:
            latest = reopened.latest()
            assert latest is not None and latest.seq == journal.max_seq() == 1
            reopened.verify(journal)
        finally:
            reopened.close()


@pytest.mark.asyncio
async def test_hub_close_refuses_an_active_server_then_allows_repeated_release() -> None:
    """The synchronous release cannot invalidate a bound server's checkpoint."""
    hub = SynapseHub()
    task = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        address = await hub.wait_until_serving()
        assert address[1] > 0
        with pytest.raises(RuntimeError, match="stop and await"):
            hub.close()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    hub.close()
    hub.close()
