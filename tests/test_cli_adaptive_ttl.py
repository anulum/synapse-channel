# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — adaptive lease TTL CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim

REPO_ROOT = Path(__file__).resolve().parents[1]


def _claim(task_id: str, owner: str, claimed_at: float) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="work",
        claimed_at=claimed_at,
        lease_expires_at=10_000.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=(f"src/{task_id}.py",),
        epoch=1,
    )


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    for task_id, owner, start, release in (
        ("TASK-A", "alpha", 0.0, 100.0),
        ("TASK-B", "alpha", 10.0, 210.0),
        ("TASK-C", "beta", 20.0, 420.0),
    ):
        store.append(
            EventKind.CLAIM,
            _claim(task_id, owner, start).as_dict(),
            ts=start,
            durable=True,
        )
        store.append(EventKind.RELEASE, {"task_id": task_id}, ts=release, durable=True)
    store.close()


def test_parser_wires_ttl_advice_command() -> None:
    args = cli.build_parser().parse_args(["ttl-advice", "hub.db", "--min-samples", "4"])

    assert args.command == "ttl-advice"
    assert args.db == "hub.db"
    assert args.min_samples == 4


def test_cli_ttl_advice_human_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["ttl-advice", str(db), "--min-samples", "3"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Lease TTL advice: advisory, manual TTL preserved" in out
    assert "recommended_default_seconds=600.000" in out


def test_cli_ttl_advice_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    exit_code = cli.main(["ttl-advice", str(db), "--min-samples", "3", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample_count"] == 3
    assert payload["recommended_default_seconds"] == 600.0


def test_cli_ttl_advice_reports_missing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["ttl-advice", str(tmp_path / "missing.db")])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_docs_wire_ttl_advice_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse ttl-advice ./synapse.db" in combined
    assert "manual TTL" in combined
    assert "advisory" in combined
