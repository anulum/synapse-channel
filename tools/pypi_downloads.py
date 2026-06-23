#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — daily PyPI download snapshot, recorded to a CSV time series
"""Record a daily PyPI download snapshot for this project to a CSV time series.

The package name is read from ``pyproject.toml`` (``[project] name``) so the same
script and workflow drop into any PyPI-published repository unchanged. It fetches
the per-day series from pypistats' ``/overall`` endpoint and upserts it by date
into a CSV, keeping both the ``without_mirrors`` count (the meaningful one — it
strips mirror-sync traffic) and the ``with_mirrors`` count. Upserting by date
means a missed run self-heals on the next one and history is never lost, even as
the upstream 180-day window rolls forward.

The CSV is intended to live on a side branch (``metrics``) so the default branch
stays free of daily snapshot commits; the accompanying workflow handles that.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 has no tomllib in the standard library
    import tomli as tomllib

PYPISTATS_OVERALL = "https://pypistats.org/api/packages/{package}/overall"
"""The pypistats endpoint returning the full available per-day download series."""

CATEGORIES = ("without_mirrors", "with_mirrors")
"""Recorded download categories, most-meaningful first."""

Fetch = Callable[[str], bytes]
"""A URL-to-bytes fetcher, injected so the network call is replaceable in tests."""


def detect_package(pyproject_path: Path) -> str:
    """Return the distribution name from a ``pyproject.toml``'s ``[project] name``."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    name = str(data.get("project", {}).get("name", "")).strip()
    if not name:
        raise ValueError(f"no [project] name in {pyproject_path}")
    return name


def _http_get(url: str) -> bytes:
    """Fetch a URL and return its body as bytes (the default network fetcher)."""
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https pypistats URL)
        body: bytes = response.read()
        return body


def fetch_overall(package: str, fetch: Fetch = _http_get) -> dict[str, Any]:
    """Fetch and decode the pypistats ``/overall`` payload for ``package``."""
    raw = fetch(PYPISTATS_OVERALL.format(package=package))
    decoded: dict[str, Any] = json.loads(raw)
    return decoded


def daily_counts(overall: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Reduce an ``/overall`` payload to ``{date: {category: downloads}}``.

    Rows with an unknown category, a missing date, or a non-integer count are
    skipped rather than aborting the snapshot, so one malformed row upstream does
    not lose the whole day.
    """
    counts: dict[str, dict[str, int]] = {}
    for row in overall.get("data", []):
        category = str(row.get("category", ""))
        if category not in CATEGORIES:
            continue
        date = str(row.get("date", "")).strip()
        if not date:
            continue
        try:
            downloads = int(row.get("downloads", 0))
        except (TypeError, ValueError):
            continue
        counts.setdefault(date, {})[category] = downloads
    return counts


def read_csv(path: Path) -> dict[str, dict[str, int]]:
    """Read an existing snapshot CSV into ``{date: {category: downloads}}``."""
    if not path.exists():
        return {}
    rows: dict[str, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            date = (record.get("date") or "").strip()
            if not date:
                continue
            rows[date] = {category: int(record.get(category) or 0) for category in CATEGORIES}
    return rows


def merge(
    existing: dict[str, dict[str, int]], fresh: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """Upsert ``fresh`` per-date counts onto ``existing``, preserving older dates."""
    merged: dict[str, dict[str, int]] = {date: dict(values) for date, values in existing.items()}
    for date, values in fresh.items():
        merged.setdefault(date, {}).update(values)
    return merged


def write_csv(path: Path, rows: dict[str, dict[str, int]]) -> None:
    """Write the per-date counts to ``path`` as a header + date-sorted CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", *CATEGORIES])
        for date in sorted(rows):
            writer.writerow([date, *(rows[date].get(category, 0) for category in CATEGORIES)])


def _summary(package: str, rows: dict[str, dict[str, int]]) -> str:
    """Return a one-line summary of the latest recorded day, or an empty-series note."""
    if not rows:
        return f"{package}: no download data available yet"
    latest = max(rows)
    without = rows[latest].get("without_mirrors", 0)
    with_mirrors = rows[latest].get("with_mirrors", 0)
    return (
        f"{package}: {len(rows)} days recorded; latest {latest} "
        f"without_mirrors={without} with_mirrors={with_mirrors}"
    )


def main(argv: list[str] | None = None, fetch: Fetch = _http_get) -> int:
    """Snapshot downloads for the resolved package and upsert them into ``--csv``."""
    parser = argparse.ArgumentParser(description="Record a daily PyPI download snapshot.")
    parser.add_argument("--pyproject", default="pyproject.toml", help="Path to pyproject.toml.")
    parser.add_argument("--package", default=None, help="Override the package name.")
    parser.add_argument("--csv", default=None, help="CSV time series to upsert into.")
    parser.add_argument(
        "--print-package",
        action="store_true",
        help="Print the resolved package name and exit (for shell use).",
    )
    args = parser.parse_args(argv)

    package = args.package or detect_package(Path(args.pyproject))
    if args.print_package:
        print(package)
        return 0
    if not args.csv:
        parser.error("--csv is required unless --print-package is given")

    try:
        overall = fetch_overall(package, fetch)
    except urllib.error.URLError as exc:
        # A transient pypistats hiccup must not crash with a traceback or corrupt
        # the existing series; fail cleanly and let the next run backfill the day.
        print(f"{package}: could not fetch download stats: {exc}", file=sys.stderr)
        return 1
    fresh = daily_counts(overall)
    csv_path = Path(args.csv)
    merged = merge(read_csv(csv_path), fresh)
    write_csv(csv_path, merged)
    print(_summary(package, merged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
