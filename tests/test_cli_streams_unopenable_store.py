# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ingest and compact refuse an event store they cannot open
"""A directory or a foreign file given as the event store exits 2 with one line."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli


@pytest.mark.parametrize("command", [["ingest"], ["compact", "--all"]])
def test_a_directory_as_the_event_store_exits_2_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    """SQLite cannot open a directory; the command reports it instead of crashing."""
    directory = tmp_path / "hub.db"
    directory.mkdir()
    assert cli.main([command[0], str(directory), *command[1:]]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{command[0]}: cannot open event store: unable to open database file\n"
    assert list(directory.iterdir()) == []


def test_compact_refuses_a_file_that_is_not_an_event_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A foreign file is refused as unreadable plaintext and left byte-identical."""
    foreign = tmp_path / "hub.db"
    foreign.write_bytes(b"not a SQLite event store")
    assert cli.main(["compact", str(foreign), "--all"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"compact: cannot open event store: cannot open {foreign} as a plaintext")
    assert foreign.read_bytes() == b"not a SQLite event store"
