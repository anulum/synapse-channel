# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replay debugger + reproduce CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.reproduce import run_reproduction
from synapse_channel.core.state import TaskClaim

REPO_ROOT = Path(__file__).resolve().parents[1]


def _claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alice",
        "note": "start",
        "claimed_at": 10.0,
        "lease_expires_at": 100.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "repo",
        "paths": ("src/auth.py",),
        "epoch": 1,
        "version": 0,
        "checkpoint": "",
    }
    base.update(overrides)
    return TaskClaim(**base).as_dict()  # type: ignore[arg-type]


def _seed(path: Path) -> None:
    store = EventStore(path)
    store.append(EventKind.CLAIM, _claim(), ts=10.0)
    store.append(
        EventKind.CHECKPOINT,
        _claim(status="in_progress", checkpoint="step2", version=2),
        ts=11.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T1"}, ts=12.0)
    store.close()


# --- parser wiring -----------------------------------------------------------


def test_parser_wires_debug_command() -> None:
    args = cli.build_parser().parse_args(["debug", "hub.db", "--fork-at", "4", "--set", "status=x"])

    assert args.command == "debug"
    assert args.db == "hub.db"
    assert args.fork_at == 4
    assert args.task == ""
    assert args.set == ["status=x"]


def test_parser_wires_reproduce_command() -> None:
    args = cli.build_parser().parse_args(["reproduce", "hub.db", "T1", "--expect", "abc"])

    assert args.command == "reproduce"
    assert args.task_id == "T1"
    assert args.expect == "abc"


# --- debug -------------------------------------------------------------------


def test_cli_debug_markdown_infers_task(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--fork-at", "2"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Fork: T1 @ seq 2" in out
    assert "- status: in_progress" in out


def test_cli_debug_json_applies_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["debug", str(db), "--task", "T1", "--fork-at", "2", "--set", "status=blocked", "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["held"] is True
    assert payload["resume"]["status"] == "blocked"


def test_cli_debug_not_held_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--task", "T1", "--fork-at", "3"])

    assert exit_code == 1
    assert "nothing to fork" in capsys.readouterr().out


def test_cli_debug_uninferable_seq_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--fork-at", "999"])

    assert exit_code == 2
    assert "no task found at seq 999" in capsys.readouterr().err


def test_cli_debug_invalid_override_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--task", "T1", "--fork-at", "2", "--set", "noequals"])

    assert exit_code == 2
    assert "expected key=value" in capsys.readouterr().err


def test_cli_debug_empty_override_key_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--task", "T1", "--fork-at", "2", "--set", "=value"])

    assert exit_code == 2
    assert "expected key=value" in capsys.readouterr().err


def test_cli_debug_unoverridable_field_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--task", "T1", "--fork-at", "2", "--set", "epoch=9"])

    assert exit_code == 2
    assert "cannot override epoch" in capsys.readouterr().err


def test_cli_debug_negative_seq_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["debug", str(db), "--task", "T1", "--fork-at=-1"])

    assert exit_code == 2
    assert "non-negative" in capsys.readouterr().err


# --- reproduce ---------------------------------------------------------------


def test_cli_reproduce_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["reproduce", str(db), "T1"])

    assert exit_code == 0
    assert "# Reproduce: T1" in capsys.readouterr().out


def test_cli_reproduce_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["reproduce", str(db), "T1", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["final_status"] == "released"


def test_cli_reproduce_expect_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    digest = run_reproduction(db, "T1").digest

    exit_code = cli.main(["reproduce", str(db), "T1", "--expect", digest])

    assert exit_code == 0
    assert "digest matches" in capsys.readouterr().err


def test_cli_reproduce_expect_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["reproduce", str(db), "T1", "--expect", "deadbeef"])

    assert exit_code == 1
    assert "digest mismatch" in capsys.readouterr().err


def test_cli_reproduce_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["reproduce", str(tmp_path / "absent.db"), "T1"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_reproduce_unknown_task_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["reproduce", str(db), "ghost"])

    assert exit_code == 2
    assert "no authoritative events" in capsys.readouterr().err


# --- documentation wiring ----------------------------------------------------


def test_docs_wire_replay_commands() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse debug" in combined
    assert "synapse reproduce" in combined
    assert "--fork-at" in combined
