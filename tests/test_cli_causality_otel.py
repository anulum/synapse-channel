# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

"""OpenTelemetry export and watch tests for the causality CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from causality_helpers import _federated_pair, _seed
from synapse_channel import cli, cli_causality
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_parser_defaults_otel_flags_to_none() -> None:
    args = cli.build_parser().parse_args(["causality", "otel", "hub.db"])

    assert args.direction == "otel"
    assert args.out is None
    assert args.endpoint is None
    assert args.service_name is None
    assert args.filter == []
    assert args.watch is False
    assert args.interval == 2.0
    assert args.count == 0


def test_cli_otel_writes_span_records_to_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(out)])

    assert exit_code == 0
    assert "exported 11 span(s) across 3 trace(s)" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 3
    linked = [span for span in payload["spans"] if span["links"]]
    assert linked


def test_cli_otel_requires_exactly_one_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    neither = cli.main(["causality", "otel", str(db)])
    assert neither == 2
    assert "exactly one of --out FILE or --endpoint URL" in capsys.readouterr().err

    both = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "s.json"),
            "--endpoint",
            "http://c:4318/v1/traces",
        ]
    )
    assert both == 2


def test_cli_otel_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--peer", f"peer={peer}"]
    )

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err


def test_cli_otel_flags_are_refused_outside_otel_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--out", str(tmp_path / "s.json")])

    assert exit_code == 2
    assert "belong to the otel mode" in capsys.readouterr().err

    for flag in (["--service-name", "hub-eu"], ["--filter", "B"]):
        exit_code = cli.main(["causality", "causes", str(db), "4", *flag])
        assert exit_code == 2
        assert "belong to the otel mode" in capsys.readouterr().err

    exit_code = cli.main(["causality", "causes", str(db), "4", "--watch"])
    assert exit_code == 2
    assert "--watch re-runs the otel or health mode" in capsys.readouterr().err


def test_cli_otel_service_name_flows_into_the_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(out), "--service-name", "hub-eu"]
    )

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["service_name"] == "hub-eu"
    assert all(span["attributes"]["service.name"] == "hub-eu" for span in payload["spans"])


def test_cli_otel_filter_narrows_and_reports_the_exclusions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(out), "--filter", "B"])

    assert exit_code == 0
    assert "1 trace(s)" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 1
    assert payload["filtered_out_tasks"] == 2


def test_cli_otel_filter_summary_counts_filtered_tasks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--filter", "B"]
    )

    assert exit_code == 0
    assert "2 task(s) filtered out" in capsys.readouterr().out


def test_cli_otel_watch_reexports_for_count_ticks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(out),
            "--watch",
            "--count",
            "2",
            "--interval",
            "0.01",
        ]
    )

    assert exit_code == 0
    summaries = capsys.readouterr().out.strip().splitlines()
    assert len(summaries) == 2
    assert all("exported 11 span(s)" in line for line in summaries)
    assert json.loads(out.read_text(encoding="utf-8"))["trace_count"] == 3


def test_cli_otel_watch_stops_on_a_failing_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    slept: list[float] = []
    args = cli.build_parser().parse_args(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "no-such-dir" / "s.json"),
            "--watch",
        ]
    )

    exit_code = cli_causality._watch_otel(args, sleeper=slept.append)

    assert exit_code == 2
    assert slept == []
    assert "cannot write span records" in capsys.readouterr().err


def test_cli_otel_watch_interrupt_is_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    def _interrupt(args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_causality, "_otel_once", _interrupt)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--watch"]
    )

    assert exit_code == 0


def test_cli_otel_watch_refuses_a_non_positive_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "s.json"),
            "--watch",
            "--interval",
            "0",
        ]
    )

    assert exit_code == 2
    assert "--interval must be positive" in capsys.readouterr().err


def test_cli_otel_filter_refuses_an_unrecorded_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--filter", "NOPE"]
    )

    assert exit_code == 2
    assert "task(s) not recorded in the log: NOPE" in capsys.readouterr().err


def test_cli_otel_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(
        ["causality", "otel", str(tmp_path / "absent.db"), "--out", str(tmp_path / "s.json")]
    )

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_otel_unwritable_out_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "no-such-dir" / "s.json")]
    )

    assert exit_code == 2
    assert "cannot write span records" in capsys.readouterr().err


def test_cli_otel_pushes_to_an_endpoint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    pushed: list[tuple[object, str]] = []

    def _fake_push(projection: object, endpoint: str) -> int:
        pushed.append((projection, endpoint))
        return 11

    monkeypatch.setattr("synapse_channel.otel_export.push_projection", _fake_push)

    exit_code = cli.main(["causality", "otel", str(db), "--endpoint", "http://c:4318/v1/traces"])

    assert exit_code == 0
    assert pushed and pushed[0][1] == "http://c:4318/v1/traces"
    assert "to http://c:4318/v1/traces" in capsys.readouterr().out


def test_cli_otel_failed_push_exits_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    def _refuse(projection: object, endpoint: str) -> int:
        raise RuntimeError("OTLP export failed: collector down")

    monkeypatch.setattr("synapse_channel.otel_export.push_projection", _refuse)

    exit_code = cli.main(["causality", "otel", str(db), "--endpoint", "http://c:4318/v1/traces"])

    assert exit_code == 2
    assert "collector down" in capsys.readouterr().err


def test_cli_otel_reports_skipped_taskless_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {}, ts=2.0)
    store.close()

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(tmp_path / "s.json")])

    assert exit_code == 0
    assert "1 taskless event(s) skipped" in capsys.readouterr().out
