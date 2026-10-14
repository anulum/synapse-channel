# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real advisory ranking CLI journeys
"""Exercise native command discovery, subprocess output and caller-safe refusal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("author", ["alpha", "agent\x1b]0;changed\x07\nline"])
def test_cli_and_fresh_process_rank_real_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_output: bool, author: str
) -> None:
    path = tmp_path / "journal.db"
    store = EventStore(path)
    store.append("ledger_task", {"task_id": "T", "title": "task"}, ts=1)
    store.append(
        "ledger_progress",
        {
            "task_id": "T",
            "author": author,
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_api.py -q",
        },
        ts=2,
    )
    store.close()
    before = path.read_bytes()
    args = ["reputation", str(path), "--agent", author, "--since", "1", "--as-of", "3"]
    if json_output:
        args.append("--json")
    assert cli.main(args) == 0
    observed = capsys.readouterr()
    child = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert child.returncode == 0 and child.stderr == observed.err == ""
    assert child.stdout == observed.out
    if json_output:
        data = json.loads(child.stdout)
        assert data["agents"][0]["net"] == 1
        assert data["agents"][0]["evidence_seqs"] == [2]
        assert data["authorises_execution"] is False
    else:
        assert f"#1 {json.dumps(author)[1:-1]}: net=1" in child.stdout
    assert "\x1b" not in child.stdout and "\x07" not in child.stdout
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--as-of", "nan"],
        ["--since", "inf"],
        ["--max-events", "0"],
        ["--row-mac-key-file", "missing-key"],
    ],
)
def test_cli_refusals_never_leak_exception_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str]
) -> None:
    path = tmp_path / "sensitive-private-sentinel" / "absent.db"
    assert cli.main(["reputation", str(path), *flags]) == 2
    observed = capsys.readouterr()
    assert observed.out == ""
    assert (
        observed.err == "Advisory ranking refused; check the journal, keys, clocks and budgets.\n"
    )
    assert not path.exists()


def test_empty_cli_and_discovery(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "empty.db"
    EventStore(path).close()
    assert cli.main(["reputation", str(path), "--through-seq", "0"]) == 0
    assert "No agent evidence found." in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_result:
        cli.main(["reputation", "--help"])
    assert exit_result.value.code == 0
    assert "--row-mac-key-file" in capsys.readouterr().out
