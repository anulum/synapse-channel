# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark scorecard regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel.benchmark.probes import ProbeResult
from synapse_channel.benchmark.scorecard import (
    NON_ISOLATED_LABEL,
    capture_host_context,
    finish_scorecard,
    render_scorecard_human,
    scorecard_to_json,
    write_scorecard,
)


def _result() -> ProbeResult:
    return ProbeResult(
        name="encode-lite",
        iterations=10,
        duration_seconds=0.01,
        metrics={"messages_per_second": 1000.0, "lite_to_raw_ratio": 0.72},
        notes=("chat envelopes with task ids",),
    )


def test_host_context_captures_real_environment() -> None:
    context = capture_host_context()
    assert context.package_version == synapse_channel.__version__
    assert context.python.count(".") == 2
    assert context.cpu_count > 0
    assert len(context.load_before) == 3
    assert context.isolation == NON_ISOLATED_LABEL
    assert context.started_at > 0


def test_missing_proc_files_report_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "synapse_channel.benchmark.scorecard._CPUINFO_PATH", tmp_path / "absent-cpuinfo"
    )
    monkeypatch.setattr(
        "synapse_channel.benchmark.scorecard._GOVERNOR_PATH", tmp_path / "absent-governor"
    )
    context = capture_host_context()
    assert context.cpu_model == "unknown"
    assert context.governor == "unknown"


def test_cpuinfo_without_model_name_reports_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nflags : fpu\n", encoding="utf-8")
    monkeypatch.setattr("synapse_channel.benchmark.scorecard._CPUINFO_PATH", cpuinfo)
    assert capture_host_context().cpu_model == "unknown"


def test_unavailable_load_average_reports_zeros(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> tuple[float, float, float]:
        raise OSError("no loadavg on this platform")

    import os as os_mod

    # On Windows ``os.getloadavg`` is absent; create a stub so setattr works, then
    # refuse. getattr-based production code already returns zeros when missing.
    if not hasattr(os_mod, "getloadavg"):
        monkeypatch.setattr(os_mod, "getloadavg", refuse, raising=False)
    else:
        monkeypatch.setattr(os_mod, "getloadavg", refuse)
    context = capture_host_context()
    assert context.load_before == (0.0, 0.0, 0.0)


def test_finish_scorecard_restamps_only_the_post_run_load() -> None:
    context = capture_host_context()
    scorecard = finish_scorecard(context, (_result(),))
    assert scorecard.context.started_at == context.started_at
    assert scorecard.context.load_before == context.load_before
    assert len(scorecard.context.load_after) == 3
    assert scorecard.results == (_result(),)


def test_json_projection_is_stable_and_labelled() -> None:
    scorecard = finish_scorecard(capture_host_context(), (_result(),))
    payload = scorecard_to_json(scorecard)
    assert payload["note"] == "installed-version scorecard; numbers are host-dependent"
    context = payload["context"]
    assert isinstance(context, dict)
    assert context["isolation"] == NON_ISOLATED_LABEL
    results = payload["results"]
    assert isinstance(results, list)
    assert set(results[0]) == {"name", "iterations", "duration_seconds", "metrics", "notes"}


def test_write_scorecard_creates_parents_and_valid_json(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "scorecard.json"
    write_scorecard(target, finish_scorecard(capture_host_context(), (_result(),)))
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["results"][0]["name"] == "encode-lite"


def test_human_rendering_carries_context_and_notes() -> None:
    text = render_scorecard_human(finish_scorecard(capture_host_context(), (_result(),)))
    assert text.startswith(f"synapse-channel {synapse_channel.__version__} benchmark scorecard")
    assert f"isolation: {NON_ISOLATED_LABEL}" in text
    assert "encode-lite: 10 iterations in 0.010s" in text
    assert "note: chat envelopes with task ids" in text
