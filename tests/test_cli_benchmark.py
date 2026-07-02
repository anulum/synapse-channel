# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark CLI command regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.benchmark.probes import PROBES
from synapse_channel.benchmark.scorecard import NON_ISOLATED_LABEL
from synapse_channel.cli import build_parser, main


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_list_names_every_probe(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["benchmark", "--list"], capsys)
    assert code == 0
    assert err == ""
    for name in PROBES:
        assert name in out
    assert "default" in out


def test_single_probe_human_scorecard(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["benchmark", "--probe", "encode-lite", "--iterations", "10"], capsys)
    assert code == 0
    assert "benchmark scorecard" in out
    assert f"isolation: {NON_ISOLATED_LABEL}" in out
    assert "encode-lite: 10 iterations" in out


def test_json_scorecard_parses_and_carries_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run(
        ["benchmark", "--probe", "encode-lite", "--iterations", "10", "--json"], capsys
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["context"]["isolation"] == NON_ISOLATED_LABEL
    assert payload["results"][0]["name"] == "encode-lite"
    assert payload["results"][0]["iterations"] == 10


def test_results_file_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out" / "scorecard.json"
    code, _, _ = _run(
        [
            "benchmark",
            "--probe",
            "encode-lite",
            "--iterations",
            "10",
            "--results",
            str(target),
        ],
        capsys,
    )
    assert code == 0
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["results"][0]["metrics"]["lite_to_raw_ratio"] < 1


def test_unknown_probe_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["benchmark", "--probe", "nonesuch"], capsys)
    assert code == 2
    assert out == ""
    assert "unknown probe" in err


def test_non_positive_iterations_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(["benchmark", "--probe", "encode-lite", "--iterations", "0"], capsys)
    assert code == 2
    assert "iterations must be positive" in err


def test_live_hub_probe_through_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    # The real-socket boundary through the full command path: a hub is
    # started, an agent connects, and round-trips are measured.
    code, out, _ = _run(
        ["benchmark", "--probe", "hub-roundtrip", "--iterations", "3", "--json"], capsys
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["results"][0]["metrics"]["roundtrips_per_second"] > 0


def test_parser_flags_and_defaults() -> None:
    parser = build_parser(command="benchmark")
    args = parser.parse_args(["benchmark"])
    assert args.probe is None
    assert args.iterations is None
    assert args.results is None
    assert args.list is False
    assert args.json is False
