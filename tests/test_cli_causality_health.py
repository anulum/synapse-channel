# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

"""Health anomaly and watch tests for the causality CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from causality_helpers import _federated_pair, _seed, _seed_clean_lifecycle
from synapse_channel import cli, cli_causality
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_cli_health_flags_anomalies_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "eve", "status": "claimed", "paths": [], "worktree": "w"},
        ts=9.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "C"}, ts=99999.0)
    store.close()

    exit_code = cli.main(["causality", "health", str(db)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "# Causal health:" in out
    assert "task=X owner=eve" in out


def test_cli_health_clean_log_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=2.0)
    store.close()

    exit_code = cli.main(["causality", "health", str(db)])

    assert exit_code == 0
    assert "0 anomalies" in capsys.readouterr().out


def test_cli_health_json_carries_the_signals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "health", str(db), "--json", "--stale-after", "1"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["note"] == "recorded-event signals, not verdicts"
    assert payload["stale_after"] == 1.0


def test_cli_health_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "health", str(db), "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err


def test_cli_health_refuses_a_non_positive_stale_after(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "health", str(db), "--stale-after", "0"])

    assert exit_code == 2
    assert "--stale-after must be positive" in capsys.readouterr().err


def test_cli_stale_after_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--stale-after", "60"])

    assert exit_code == 2
    assert "--stale-after belongs to the health mode" in capsys.readouterr().err


def test_cli_health_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "health", str(tmp_path / "absent.db")])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_health_watch_prints_one_baseline_and_stays_quiet_when_steady(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--watch", "--count", "3", "--interval", "0.01"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.count("# Causal health:") == 1
    assert "orphaned claim seq=" not in out  # the fact format appears only on transitions


def test_cli_health_watch_reports_new_and_cleared_anomalies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "eve", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()

    def _mutate(_delay: float) -> None:
        # between ticks the orphan X resolves and a fresh orphan Y appears
        added = EventStore(db)
        added.append(EventKind.RELEASE, {"task_id": "X"}, ts=2.0)
        added.append(
            EventKind.CLAIM,
            {"task_id": "Y", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
            ts=3.0,
        )
        added.close()

    args = cli.build_parser().parse_args(
        ["causality", "health", str(db), "--watch", "--count", "2"]
    )
    exit_code = cli_causality._watch_health(args, stale_after=900.0, sleeper=_mutate)

    assert exit_code == 1  # the last tick still sees the Y orphan
    out_lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("# Causal health:") for line in out_lines) == 1
    assert any(
        line.startswith("+ orphaned claim") and "task=Y owner=bob" in line for line in out_lines
    )
    assert any(
        line.startswith("- orphaned claim") and "task=X owner=eve" in line for line in out_lines
    )


def test_cli_health_watch_json_streams_one_report_per_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--watch", "--json", "--count", "2", "--interval", "0.01"]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["anomaly_count"] == 0 for line in lines)


def test_cli_health_watch_refuses_a_non_positive_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(["causality", "health", str(db), "--watch", "--interval", "0"])

    assert exit_code == 2
    assert "--interval must be positive" in capsys.readouterr().err


def test_cli_health_watch_missing_store_fails_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "health", str(tmp_path / "absent.db"), "--watch"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_health_watch_interrupt_is_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    def _interrupt(*args: object, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_causality, "_watch_health", _interrupt)

    exit_code = cli.main(["causality", "health", str(db), "--watch"])

    assert exit_code == 0


def test_cli_health_since_bounds_the_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "OLD", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
        ts=10.0,
    )
    store.append(
        EventKind.CLAIM,
        {"task_id": "NEW", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=500.0,
    )
    store.close()

    full = cli.main(["causality", "health", str(db)])
    full_out = capsys.readouterr().out
    windowed = cli.main(["causality", "health", str(db), "--since", "400"])
    windowed_out = capsys.readouterr().out

    assert full == 1 and "task=OLD" in full_out
    assert windowed == 1  # NEW's orphan is inside the window
    assert "task=OLD" not in windowed_out
    assert "task=NEW" in windowed_out


def test_cli_since_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--since", "1"])

    assert exit_code == 2
    assert "--since belongs to the health mode" in capsys.readouterr().err


def test_cli_health_textfile_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "ORPH", "owner": "a", "status": "claimed", "paths": [], "worktree": "w"},
        ts=10.0,
    )
    store.close()
    out_file = tmp_path / "health.prom"

    exit_code = cli.main(["causality", "health", str(db), "--textfile", str(out_file)])

    assert exit_code == 1  # the orphan is an anomaly
    assert f"causal-health metrics written to {out_file}" in capsys.readouterr().out
    text = out_file.read_text(encoding="utf-8")
    assert 'synapse_causal_health_anomalies{shape="orphaned"} 1' in text


def test_cli_health_textfile_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--textfile", "x.prom"])

    assert exit_code == 2
    assert "--textfile belongs to the health mode" in capsys.readouterr().err


def test_cli_health_textfile_does_not_compose_with_watch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--textfile", "x.prom", "--watch", "--count", "1"]
    )

    assert exit_code == 2
    assert "does not compose with --watch" in capsys.readouterr().err


def test_cli_health_textfile_reports_an_unwritable_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "a", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()
    blocker = tmp_path / "occupied"
    blocker.write_text("not a dir", encoding="utf-8")

    exit_code = cli.main(["causality", "health", str(db), "--textfile", str(blocker / "out.prom")])

    assert exit_code == 2
    assert "cannot write the textfile metrics" in capsys.readouterr().err
