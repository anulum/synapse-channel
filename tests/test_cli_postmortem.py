# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — postmortem CLI regressions

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
        task_id="BUG-1",
        owner="debugger",
        note="investigate",
        claimed_at=10.0,
        lease_expires_at=100.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("src/bug.py",),
        epoch=1,
    )


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append(EventKind.CLAIM, _claim().as_dict(), ts=10.0, durable=True)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "BUG-1",
            "author": "debugger",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest tests/test_cli_postmortem.py -q",
            "posted_at": 11.0,
        },
        ts=11.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "BUG-1"}, ts=12.0, durable=True)
    store.close()


def test_parser_wires_postmortem_command() -> None:
    args = cli.build_parser().parse_args(["postmortem", "hub.db", "BUG-1"])

    assert args.command == "postmortem"
    assert args.db == "hub.db"
    assert args.task_id == "BUG-1"


def test_cli_postmortem_markdown_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["postmortem", str(db), "BUG-1"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Postmortem: BUG-1" in out
    assert "release receipt: evidence=pytest tests/test_cli_postmortem.py -q" in out


def test_cli_postmortem_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["postmortem", str(db), "BUG-1", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "BUG-1"
    assert payload["releases"][0]["kind"] == "release"


def test_cli_postmortem_reports_missing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["postmortem", str(tmp_path / "missing.db"), "BUG-1"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_docs_wire_postmortem_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse postmortem ./synapse.db TASK-1" in combined
    assert "replayable postmortem" in combined
    assert "candidate unanswered messages" in combined
