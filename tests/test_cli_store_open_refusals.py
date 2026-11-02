# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — merkle and compute suggestions refuse a store SQLite cannot open
"""A directory or an unreadable file as the event store exits 2 with one line."""

from __future__ import annotations

from pathlib import Path

import pytest

from _platform_caps import requires_posix_mode_bits
from synapse_channel import cli
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize(
    "command", [["merkle", "root"], ["merkle", "prove"], ["merkle", "checkpoint"]]
)
def test_merkle_refuses_a_directory_as_the_event_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    """SQLite cannot open a directory; each merkle action reports it instead of crashing."""
    directory = tmp_path / "hub.db"
    directory.mkdir()
    # checkpoint opens the event store only to verify against an existing checkpoint store.
    checkpoint_db = tmp_path / "checkpoints.db"
    MerkleCheckpointStore(checkpoint_db).close()
    extra = {
        "root": [],
        "prove": ["1"],
        "checkpoint": ["--checkpoint-db", str(checkpoint_db), "--verify"],
    }[command[1]]
    assert cli.main([*command, str(directory), *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "unable to open database file\n"
    assert list(directory.iterdir()) == []


@requires_posix_mode_bits
def test_compute_suggestion_refuses_an_unreadable_event_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real store the caller cannot read is reported as an entitlements error."""
    store = tmp_path / "hub.db"
    EventStore(store).close()
    tasks = tmp_path / "tasks.json"
    tasks.write_text('{"tasks": []}', encoding="utf-8")
    tasks.chmod(0o600)
    store.chmod(0o000)
    try:
        code = cli.main(
            [
                "entitlements",
                "suggest-compute",
                "--file",
                str(tasks),
                "--hub-db",
                str(store),
                "--reviewer",
                "operator:me",
            ]
        )
    finally:
        store.chmod(0o600)
    assert code == 2
    assert capsys.readouterr().err == "entitlements: unable to open database file\n"
