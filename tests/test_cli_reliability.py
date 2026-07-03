# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reliability CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim

REPO_ROOT = Path(__file__).resolve().parents[1]


def _claim() -> TaskClaim:
    return TaskClaim(
        task_id="STALE",
        owner="alpha",
        note="work",
        claimed_at=1.0,
        lease_expires_at=2.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("src/auth.py",),
        epoch=1,
    )


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append(EventKind.CLAIM, _claim().as_dict(), ts=1.0, durable=True)
    store.close()


def test_parser_wires_reliability_command() -> None:
    args = cli.build_parser().parse_args(["reliability", "hub.db", "--as-of", "10"])

    assert args.command == "reliability"
    assert args.db == "hub.db"
    assert args.as_of == 10.0


def test_cli_reliability_human_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["reliability", str(db), "--as-of", "10"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Reliability memory: audit signals, not scores" in out
    assert "stale_claim" in out


def test_cli_reliability_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["reliability", str(db), "--as-of", "10", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["findings"][0]["kind"] == "stale_claim"
    assert payload["owners"][0]["owner"] == "alpha"


def test_cli_reliability_reports_missing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["reliability", str(tmp_path / "missing.db")])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_docs_wire_reliability_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse reliability ./synapse.db" in combined
    assert "audit signals, not scores" in combined
    assert "stale claims" in combined


def test_cli_reliability_textfile_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    out_file = tmp_path / "reliability.prom"

    exit_code = cli.main(["reliability", str(db), "--as-of", "10", "--textfile", str(out_file)])

    assert exit_code == 0
    assert f"reliability metrics written to {out_file}" in capsys.readouterr().out
    text = out_file.read_text(encoding="utf-8")
    assert "# TYPE synapse_reliability_findings gauge" in text
    assert 'synapse_reliability_findings{kind="stale_claim"}' in text


def test_cli_reliability_textfile_reports_an_unwritable_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    blocker = tmp_path / "occupied"
    blocker.write_text("not a dir", encoding="utf-8")

    exit_code = cli.main(["reliability", str(db), "--textfile", str(blocker / "out.prom")])

    assert exit_code == 2
    assert "cannot write the textfile metrics" in capsys.readouterr().err
