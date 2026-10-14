# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real compliance CLI wiring and safe refusals
"""Exercise every verb through both the public CLI and a fresh interpreter."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.event_row_mac import load_or_create_row_mac_key, row_mac_key_path
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize("action", ["export", "retention", "report"])
@pytest.mark.parametrize("json_output", [False, True])
def test_real_cli_verbs_and_subprocess_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action: str,
    json_output: bool,
) -> None:
    path = tmp_path / "cli.db"
    store = EventStore(path)
    store.append("finding", {"task_id": "T", "private": "sentinel-hidden"}, ts=1)
    store.close()
    argv = ["compliance", action, str(path)]
    if json_output:
        argv.append("--json")
    if action != "export":
        argv.extend(["--as-of", "20000000"])
    before = path.read_bytes()
    assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert output.err == "" and "sentinel-hidden" not in output.out
    child = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=15,
        check=False,
    )
    assert child.returncode == 0 and child.stderr == ""
    assert child.stdout == output.out
    assert path.read_bytes() == before
    if json_output:
        data = json.loads(child.stdout)
        assert isinstance(data, dict)
        if action == "retention":
            assert data["reaches_floor"]
        elif action == "export":
            assert data["entry_count"] == 1
        else:
            assert data["framework"] == "eu-ai-act"


@pytest.mark.parametrize("action", ["export", "retention", "report"])
def test_cli_refuses_storage_without_exposing_exception_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    private = tmp_path / "private-sentinel" / "absent.db"
    assert cli.main(["compliance", action, str(private), "--json"]) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert (
        result.err
        == "Record observation refused; check the journal, keys, clock and export budgets.\n"
    )
    assert "private-sentinel" not in result.err
    assert not private.parent.exists()


def test_cli_clock_and_event_budgets_refuse_complete_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "budgets.db"
    store = EventStore(path)
    store.append_batch((("claim", {"task_id": "T"}), ("release", {"task_id": "T"})), ts=1)
    store.close()
    for argv in (
        ["export", "--max-events", "1"],
        ["report", "--as-of", "nan"],
        ["retention", "--as-of", "inf"],
        ["export", "--max-payload-bytes", "1"],
    ):
        assert cli.main(["compliance", argv[0], str(path), *argv[1:]]) == 2
        output = capsys.readouterr()
        assert output.out == "" and "Record observation refused" in output.err
    assert cli.main(["compliance", "report", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["framework"] == "eu-ai-act"


def test_discovery_advertises_group_and_rejects_unsupported_framework() -> None:
    parser = cli.build_parser()
    assert "compliance" in parser.format_help()
    with pytest.raises(SystemExit) as refused:
        parser.parse_args(["compliance", "report", "hub.db", "--framework", "soc2"])
    assert refused.value.code == 2


def test_authenticated_cli_refuses_nonascii_mac_and_oversized_deep_json_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "authenticated-cli.db"
    store = EventStore(path)
    key_path = row_mac_key_path(path)
    key = load_or_create_row_mac_key(key_path, current_max_seq=0, log_has_macs=False)
    store.enable_row_mac(key)
    store.append("claim", {"task_id": "T"}, ts=1)
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET mac='😃'")
    assert cli.main(["compliance", "export", str(path), "--row-mac-key-file", str(key_path)]) == 2
    assert "Record observation refused" in capsys.readouterr().err
    deep = tmp_path / "deep.db"
    store = EventStore(deep)
    store.append("claim", {"task_id": "T"}, ts=1)
    store.close()
    encoded = '{"x":' * 2000 + "0" + "}" * 2000
    with sqlite3.connect(deep) as connection:
        connection.execute("UPDATE events SET payload=?", (encoded,))
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "compliance",
            "report",
            str(deep),
            "--json",
            "--max-payload-bytes",
            "1024",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=15,
        check=False,
    )
    assert child.returncode == 2 and child.stdout == ""
    assert "Record observation refused" in child.stderr and "Traceback" not in child.stderr
