# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet-scorecard CLI regressions
"""Drive scorecard JSON and OTLP modes through the packaged CLI parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_fleet_scorecard import _collector_endpoints, _write_bundle
from synapse_channel.core.accounting import format_usage_note
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _seed(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "TASK",
            "owner": "alice",
            "status": "claimed",
            "paths": ["src/a.py"],
            "worktree": "main",
        },
        ts=1.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "TASK",
            "author": "alice",
            "kind": "usage",
            "text": format_usage_note(
                model="model-a",
                input_tokens=1000,
                output_tokens=500,
            ),
        },
        ts=2.0,
    )
    store.close()


def test_json_mode_writes_a_complete_owner_only_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = tmp_path / "hub.db"
    output = tmp_path / "reports" / "fleet.json"
    trend = tmp_path / "trend.db"
    pricing = tmp_path / "pricing.json"
    budget = tmp_path / "budget.json"
    _seed(hub)
    EventStore(trend).close()
    output.parent.mkdir()
    output.write_text("old", encoding="utf-8")
    output.chmod(0o644)
    pricing.write_text(
        json.dumps({"model-a": {"input_per_1k": 2.0, "output_per_1k": 4.0}}),
        encoding="utf-8",
    )
    budget.write_text(json.dumps({"alice": 3.0}), encoding="utf-8")

    code = main(
        [
            "fleet-scorecard",
            str(hub),
            "--pricing",
            str(pricing),
            "--budget",
            str(budget),
            "--service-name",
            "hub-test",
            "--trend",
            str(trend),
            "--out",
            str(output),
        ]
    )

    assert code == 0
    from synapse_channel.core.secure_path import assert_owner_only_file_path

    assert_owner_only_file_path(output, purpose="fleet scorecard")
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["accounting"]["totals"]["estimated_cost"] == pytest.approx(4.0)
    assert document["accounting"]["budgets"][0]["over_budget"] is True
    assert document["causality"]["service_name"] == "hub-test"
    assert document["benchmark_trend"]["runs"] == []
    assert "fleet scorecard:" in capsys.readouterr().out


def test_otlp_mode_routes_both_signal_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = tmp_path / "hub.db"
    _seed(hub)
    seen: list[tuple[str, str, float]] = []

    def push_metrics(
        points: object,
        endpoint: str,
        *,
        service_name: str,
        timeout: float,
    ) -> int:
        assert points
        seen.append(("metrics", endpoint, timeout))
        assert service_name == "hub-us"
        return 7

    def push_traces(projection: object, endpoint: str, *, timeout: float) -> int:
        assert projection
        seen.append(("traces", endpoint, timeout))
        return 3

    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard.push_metric_points", push_metrics)
    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard.push_projection", push_traces)

    code = main(
        [
            "fleet-scorecard",
            str(hub),
            "--service-name",
            "hub-us",
            "--timeout",
            "4.5",
            "--endpoint",
            "https://collector.example/tenant-a/",
        ]
    )

    assert code == 0
    assert seen == [
        ("metrics", "https://collector.example/tenant-a/v1/metrics", 4.5),
        ("traces", "https://collector.example/tenant-a/v1/traces", 4.5),
    ]
    output = capsys.readouterr().out
    assert "3 spans" in output
    assert "7 metric points" in output


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--max-nodes", "-1"], "--max-nodes must be zero or positive"),
        (["--timeout", "0"], "--timeout must be positive"),
        (["--service-name", " "], "--service-name must not be blank"),
    ],
)
def test_local_configuration_errors_fail_before_store_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    code = main(
        ["fleet-scorecard", str(tmp_path / "absent.db"), "--out", str(tmp_path / "x"), *extra]
    )

    assert code == 2
    assert message in capsys.readouterr().err


def test_report_and_output_failures_are_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.db"
    assert main(["fleet-scorecard", str(missing), "--out", str(tmp_path / "x")]) == 2
    assert "missing event store" in capsys.readouterr().err

    hub = tmp_path / "hub.db"
    _seed(hub)

    missing_trend = tmp_path / "missing-trend.db"
    assert (
        main(
            [
                "fleet-scorecard",
                str(hub),
                "--trend",
                str(missing_trend),
                "--out",
                str(tmp_path / "x"),
            ]
        )
        == 2
    )
    assert "missing trend store" in capsys.readouterr().err

    def refuse(_path: Path, _document: dict[str, object]) -> None:
        raise OSError("disk read-only")

    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard._write_bundle", refuse)
    assert main(["fleet-scorecard", str(hub), "--out", str(tmp_path / "x")]) == 2
    assert "disk read-only" in capsys.readouterr().err


@pytest.mark.parametrize("failing_signal", ["metrics", "traces"])
def test_otlp_failure_is_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_signal: str,
) -> None:
    hub = tmp_path / "hub.db"
    _seed(hub)

    def push_metrics(*_args: Any, **_kwargs: Any) -> int:
        if failing_signal == "metrics":
            raise RuntimeError("metrics refused")
        return 2

    def push_traces(*_args: Any, **_kwargs: Any) -> int:
        if failing_signal == "traces":
            raise RuntimeError("traces refused")
        return 2

    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard.push_metric_points", push_metrics)
    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard.push_projection", push_traces)

    code = main(["fleet-scorecard", str(hub), "--endpoint", "http://collector:4318"])

    assert code == 2
    assert f"{failing_signal} refused" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("endpoint", "match"),
    [
        ("collector:4318", r"http\(s\)"),
        ("http://user:secret@collector:4318", "must not embed credentials"),
        ("http://collector:4318?token=x", "query string or fragment"),
        ("http://collector:4318/#frag", "query string or fragment"),
        ("http://collector:4318/v1/traces", "omit the /v1/traces"),
        ("http://collector:4318/v1/metrics/", "omit the /v1/traces"),
    ],
)
def test_collector_base_url_is_fail_closed(endpoint: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _collector_endpoints(endpoint)


def test_atomic_writer_removes_its_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "scorecard.json"

    def refuse(_source: object, _destination: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr("synapse_channel.cli_fleet_scorecard.os.replace", refuse)

    with pytest.raises(OSError, match="replace refused"):
        _write_bundle(output, {"ok": True})
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
