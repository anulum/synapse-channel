# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the daily PyPI download snapshot tool

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "pypi_downloads.py"
_SPEC = importlib.util.spec_from_file_location("pypi_downloads", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
dl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dl)

_SAMPLE = {
    "data": [
        {"category": "without_mirrors", "date": "2026-06-21", "downloads": 100},
        {"category": "with_mirrors", "date": "2026-06-21", "downloads": 300},
        {"category": "without_mirrors", "date": "2026-06-22", "downloads": 120},
        {"category": "with_mirrors", "date": "2026-06-22", "downloads": 340},
    ],
    "package": "synapse-channel",
    "type": "overall_downloads",
}


def test_detect_package_reads_project_name(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "synapse-channel"\nversion = "0.1.0"\n')
    assert dl.detect_package(pyproject) == "synapse-channel"


def test_detect_package_missing_name_raises(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[build-system]\nrequires = ["setuptools"]\n')
    with pytest.raises(ValueError, match="no \\[project\\] name"):
        dl.detect_package(pyproject)


def test_daily_counts_parses_both_categories() -> None:
    counts = dl.daily_counts(_SAMPLE)
    assert counts["2026-06-21"] == {"without_mirrors": 100, "with_mirrors": 300}
    assert counts["2026-06-22"] == {"without_mirrors": 120, "with_mirrors": 340}


def test_daily_counts_skips_malformed_rows() -> None:
    payload: dict[str, Any] = {
        "data": [
            {"category": "weird", "date": "2026-06-21", "downloads": 9},
            {"category": "without_mirrors", "date": "", "downloads": 9},
            {"category": "with_mirrors", "date": "2026-06-21", "downloads": "NaN"},
            {"category": "without_mirrors", "date": "2026-06-21", "downloads": 7},
        ]
    }
    counts = dl.daily_counts(payload)
    assert counts == {"2026-06-21": {"without_mirrors": 7}}


def test_daily_counts_empty_payload() -> None:
    assert dl.daily_counts({}) == {}


def test_read_csv_missing_returns_empty(tmp_path: Path) -> None:
    assert dl.read_csv(tmp_path / "nope.csv") == {}


def test_read_write_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "downloads" / "synapse-channel.csv"
    rows = {
        "2026-06-22": {"without_mirrors": 120, "with_mirrors": 340},
        "2026-06-21": {"without_mirrors": 100, "with_mirrors": 300},
    }
    dl.write_csv(path, rows)
    text = path.read_text()
    assert text.splitlines()[0] == "date,without_mirrors,with_mirrors"
    # rows are written date-sorted regardless of insertion order
    assert text.splitlines()[1].startswith("2026-06-21,")
    assert dl.read_csv(path) == rows


def test_read_csv_skips_blank_date(tmp_path: Path) -> None:
    path = tmp_path / "c.csv"
    path.write_text("date,without_mirrors,with_mirrors\n,5,5\n2026-06-21,1,2\n")
    assert dl.read_csv(path) == {"2026-06-21": {"without_mirrors": 1, "with_mirrors": 2}}


def test_merge_upserts_new_date_and_updates_existing() -> None:
    existing = {"2026-06-21": {"without_mirrors": 100, "with_mirrors": 300}}
    fresh = {
        "2026-06-21": {"without_mirrors": 110},  # a later, corrected figure for the same day
        "2026-06-22": {"without_mirrors": 120, "with_mirrors": 340},
    }
    merged = dl.merge(existing, fresh)
    assert merged["2026-06-21"] == {"without_mirrors": 110, "with_mirrors": 300}
    assert merged["2026-06-22"] == {"without_mirrors": 120, "with_mirrors": 340}
    # the original mapping is not mutated
    assert existing["2026-06-21"] == {"without_mirrors": 100, "with_mirrors": 300}


def test_fetch_overall_decodes_injected_body() -> None:
    body = json.dumps(_SAMPLE).encode()
    result = dl.fetch_overall("synapse-channel", fetch=lambda url: body)
    assert result["package"] == "synapse-channel"


def test_fetch_overall_builds_endpoint_url() -> None:
    seen: dict[str, str] = {}

    def spy(url: str) -> bytes:
        seen["url"] = url
        return b"{}"

    dl.fetch_overall("my-pkg", fetch=spy)
    assert seen["url"] == "https://pypistats.org/api/packages/my-pkg/overall"


def test_http_get_reads_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"payload"

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _FakeResponse())
    assert dl._http_get("https://example.test") == b"payload"


def test_summary_empty_series() -> None:
    assert dl._summary("pkg", {}) == "pkg: no download data available yet"


def test_summary_reports_latest_day() -> None:
    rows = {
        "2026-06-21": {"without_mirrors": 100, "with_mirrors": 300},
        "2026-06-22": {"without_mirrors": 120, "with_mirrors": 340},
    }
    summary = dl._summary("pkg", rows)
    assert "latest 2026-06-22" in summary
    assert "without_mirrors=120" in summary
    assert "with_mirrors=340" in summary


def test_main_print_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "synapse-channel"\n')
    rc = dl.main(["--pyproject", str(pyproject), "--print-package"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "synapse-channel"


def test_main_writes_csv_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = tmp_path / "downloads" / "synapse-channel.csv"
    body = json.dumps(_SAMPLE).encode()
    rc = dl.main(
        ["--package", "synapse-channel", "--csv", str(csv_path)],
        fetch=lambda url: body,
    )
    assert rc == 0
    assert csv_path.exists()
    assert dl.read_csv(csv_path)["2026-06-22"] == {"without_mirrors": 120, "with_mirrors": 340}
    assert "synapse-channel: 2 days recorded" in capsys.readouterr().out


def test_main_upserts_into_existing_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "synapse-channel.csv"
    dl.write_csv(csv_path, {"2026-06-20": {"without_mirrors": 50, "with_mirrors": 90}})
    body = json.dumps(_SAMPLE).encode()
    dl.main(["--package", "synapse-channel", "--csv", str(csv_path)], fetch=lambda url: body)
    rows = dl.read_csv(csv_path)
    # the pre-existing day survives and the fetched days are added
    assert set(rows) == {"2026-06-20", "2026-06-21", "2026-06-22"}


def test_main_requires_csv_unless_printing(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "synapse-channel"\n')
    with pytest.raises(SystemExit):
        dl.main(["--pyproject", str(pyproject)])


def test_main_returns_one_on_fetch_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = tmp_path / "synapse-channel.csv"
    dl.write_csv(csv_path, {"2026-06-20": {"without_mirrors": 50, "with_mirrors": 90}})

    def boom(url: str) -> bytes:
        raise urllib.error.URLError("rate limited")

    rc = dl.main(["--package", "synapse-channel", "--csv", str(csv_path)], fetch=boom)
    assert rc == 1
    assert "could not fetch download stats" in capsys.readouterr().err
    # the existing series is left untouched on a fetch failure
    assert dl.read_csv(csv_path) == {"2026-06-20": {"without_mirrors": 50, "with_mirrors": 90}}
