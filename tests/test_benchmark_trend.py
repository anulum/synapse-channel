# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark trend store and rendering regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.benchmark.probes import ProbeResult
from synapse_channel.benchmark.scorecard import NON_ISOLATED_LABEL, HostContext, Scorecard
from synapse_channel.benchmark.trend import (
    ASCII_SPARK_LEVELS,
    SPARK_LEVELS,
    append_scorecard,
    context_breaks,
    load_history,
    render_trend_human,
    sparkline,
    trend_to_json,
)


def _context(**overrides: object) -> HostContext:
    fields: dict[str, object] = {
        "package_version": "0.91.0",
        "python": "3.12.3",
        "platform": "Linux-test",
        "cpu_model": "Test CPU 9000",
        "cpu_count": 8,
        "governor": "performance",
        "load_before": (0.5, 0.4, 0.3),
        "load_after": (0.6, 0.5, 0.4),
        "isolation": NON_ISOLATED_LABEL,
        "started_at": 1000.0,
    }
    fields.update(overrides)
    return HostContext(**fields)  # type: ignore[arg-type]


def _scorecard(
    events_per_second: float, *, started_at: float = 1000.0, **context_overrides: object
) -> Scorecard:
    result = ProbeResult(
        name="event-store-append",
        iterations=10,
        duration_seconds=0.1,
        metrics={"events_per_second": events_per_second, "p95_ms": 2.5},
    )
    return Scorecard(
        context=_context(started_at=started_at, **context_overrides), results=(result,)
    )


class TestStore:
    def test_append_and_load_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"

        first = append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        second = append_scorecard(db, _scorecard(120.0, started_at=2000.0))

        runs = load_history(db)
        assert (first, second) == (1, 2)
        assert [run.run_id for run in runs] == [1, 2]
        assert runs[0].metrics["event-store-append"]["events_per_second"] == 100.0
        assert runs[1].package_version == "0.91.0"
        assert runs[1].cpu_model == "Test CPU 9000"

    def test_history_orders_by_start_time_then_id(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=2000.0))
        append_scorecard(db, _scorecard(120.0, started_at=1000.0))

        runs = load_history(db)

        assert [run.run_id for run in runs] == [2, 1]

    def test_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing trend store"):
            load_history(tmp_path / "absent.db")

    def test_parent_directories_are_created(self, tmp_path: Path) -> None:
        db = tmp_path / "deep" / "nested" / "trend.db"

        append_scorecard(db, _scorecard(100.0))

        assert db.exists()


class TestContextBreaks:
    def test_stable_context_has_no_breaks(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(db, _scorecard(110.0, started_at=2000.0))

        assert context_breaks(load_history(db)) == ()

    def test_changed_package_cpu_and_governor_are_annotated(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(
            db,
            _scorecard(
                110.0,
                started_at=2000.0,
                package_version="0.92.0",
                cpu_model="Other CPU",
                governor="powersave",
            ),
        )

        breaks = context_breaks(load_history(db))

        assert len(breaks) == 1
        assert breaks[0].before_run_id == 2
        assert breaks[0].changes == (
            "package 0.91.0→0.92.0",
            "cpu Test CPU 9000→Other CPU",
            "governor performance→powersave",
        )


class TestSparkline:
    def test_range_maps_to_the_glyph_extremes(self) -> None:
        line = sparkline([0.0, 100.0])
        assert line == SPARK_LEVELS[0] + SPARK_LEVELS[-1]

    def test_flat_series_renders_mid_level(self) -> None:
        assert sparkline([5.0, 5.0, 5.0]) == SPARK_LEVELS[3] * 3

    def test_empty_series_renders_nothing(self) -> None:
        assert sparkline([]) == ""

    def test_ascii_range_maps_to_the_glyph_extremes(self) -> None:
        line = sparkline([0.0, 100.0], ASCII_SPARK_LEVELS)
        assert line == ASCII_SPARK_LEVELS[0] + ASCII_SPARK_LEVELS[-1]

    def test_ascii_flat_series_renders_the_middle_glyph(self) -> None:
        middle = ASCII_SPARK_LEVELS[(len(ASCII_SPARK_LEVELS) - 1) // 2]
        assert sparkline([5.0, 5.0, 5.0], ASCII_SPARK_LEVELS) == middle * 3

    def test_ascii_ramp_is_pure_distinct_ascii(self) -> None:
        assert ASCII_SPARK_LEVELS.isascii()
        assert ASCII_SPARK_LEVELS.isprintable()
        assert len(set(ASCII_SPARK_LEVELS)) == len(ASCII_SPARK_LEVELS)

    def test_empty_glyph_ramp_is_refused(self) -> None:
        with pytest.raises(ValueError, match="glyph level"):
            sparkline([1.0], "")


class TestRenderings:
    def test_trend_lines_carry_sparkline_and_range(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(db, _scorecard(150.0, started_at=2000.0))
        append_scorecard(db, _scorecard(120.0, started_at=3000.0))

        text = render_trend_human(load_history(db))

        assert "Benchmark trend: 3 stored run(s)" in text
        assert "event-store-append events_per_second: " in text
        assert "100.00 → 120.00 (min 100.00, max 150.00, 3 runs)" in text
        assert "event-store-append p95_ms: " in text

    def test_break_annotation_precedes_the_series(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(db, _scorecard(110.0, started_at=2000.0, package_version="0.92.0"))

        text = render_trend_human(load_history(db))

        assert "context break before run 2: package 0.91.0→0.92.0" in text

    def test_single_run_reports_no_trend_yet(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0))

        text = render_trend_human(load_history(db))

        assert "event-store-append events_per_second: 100.00 (1 run — no trend yet)" in text

    def test_empty_history_renders_a_plain_statement(self) -> None:
        assert render_trend_human(()) == "Benchmark trend: no stored runs."

    def test_ascii_rendering_is_pure_printable_ascii(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(db, _scorecard(150.0, started_at=2000.0, package_version="0.92.0"))

        text = render_trend_human(load_history(db), ascii_glyphs=True)

        assert text.isascii()
        assert "context break before run 2: package 0.91.0->0.92.0" in text
        assert "100.00 -> 150.00 (min 100.00, max 150.00, 2 runs)" in text
        assert not any(glyph in text for glyph in SPARK_LEVELS)
        assert any(glyph in text for glyph in ASCII_SPARK_LEVELS)

    def test_ascii_single_run_degrades_the_dash(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0))

        text = render_trend_human(load_history(db), ascii_glyphs=True)

        assert "event-store-append events_per_second: 100.00 (1 run -- no trend yet)" in text
        assert text.isascii()

    def test_run_missing_a_metric_shortens_that_series_only(self, tmp_path: Path) -> None:
        # a --probe-narrowed run stores fewer probes; the other series keep
        # their full length and the narrowed one reports what it has
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        narrow = Scorecard(
            context=_context(started_at=2000.0),
            results=(
                ProbeResult(
                    name="encode-lite",
                    iterations=10,
                    duration_seconds=0.1,
                    metrics={"messages_per_second": 9000.0},
                ),
            ),
        )
        append_scorecard(db, narrow)

        text = render_trend_human(load_history(db))

        assert "event-store-append events_per_second: 100.00 (1 run — no trend yet)" in text
        assert "encode-lite messages_per_second: 9,000.00 (1 run — no trend yet)" in text

    def test_json_carries_runs_breaks_and_the_note(self, tmp_path: Path) -> None:
        db = tmp_path / "trend.db"
        append_scorecard(db, _scorecard(100.0, started_at=1000.0))
        append_scorecard(db, _scorecard(110.0, started_at=2000.0, governor="powersave"))

        payload = trend_to_json(load_history(db))

        assert payload["note"] == "host-dependent series; compare within one context segment"
        runs = payload["runs"]
        assert isinstance(runs, list)
        assert runs[0]["metrics"]["event-store-append"]["events_per_second"] == 100.0
        breaks = payload["context_breaks"]
        assert isinstance(breaks, list)
        assert breaks[0] == {
            "before_run_id": 2,
            "changes": ["governor performance→powersave"],
        }
