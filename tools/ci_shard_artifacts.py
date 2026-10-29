#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — complete indexed CI artifact and coverage union gate
"""Publish only completed shards, verify every index, and gate the complete coverage union."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from coverage import Coverage
from coverage.exceptions import CoverageException

NATIVE_PREFIX = "tests/fixtures/opencode/metadata_denial_linux.py::"


def _read(path: Path) -> dict[str, Any]:
    """Require an ordinary JSON object from a downloaded indexed artifact."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing ordinary artifact: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact must contain a JSON object")
    return cast("dict[str, Any]", value)


def _ids(record: dict[str, Any], key: str) -> list[str]:
    """Reject duplicate, empty or unsorted inventory entries."""
    value = record.get(key)
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or value != sorted(set(value))
    ):
        raise ValueError(f"invalid {key} test inventory")
    return cast("list[str]", value)


def _completed(record: dict[str, Any]) -> None:
    """Require the exact partition and every actual completed pytest execution."""
    inventory = _ids(record, "inventory")
    selected = _ids(record, "selected")
    index, count = record.get("index"), record.get("count")
    if (
        record.get("schema") != 1
        or record.get("success") is not True
        or record.get("exitstatus") != 0
        or type(index) is not int
        or type(count) is not int
        or not 1 <= count <= 128
        or not 0 <= index < count
        or not selected
        or selected != inventory[index::count]
        or _ids(record, "completed") != selected
    ):
        raise ValueError("incomplete or misidentified pytest shard")


def _native(main: dict[str, Any], native: dict[str, Any] | None) -> None:
    """Require the actual non-skipped native follow-up exactly once per interpreter."""
    if main["index"] != 0:
        if native is not None:
            raise ValueError("native follow-up belongs only to shard zero")
        return
    if native is None:
        raise ValueError("missing mandatory native metadata-refusal follow-up")
    if not isinstance(native, dict):
        raise ValueError("malformed native metadata-refusal follow-up")
    _completed(native)
    for key in ("source", "run_id", "attempt", "python", "config_sha256"):
        if native.get(key) != main.get(key):
            raise ValueError(f"native follow-up has a different {key}")
    if (
        native["count"] != 1
        or native["index"] != 0
        or _ids(native, "skipped")
        or _ids(native, "called") != native["selected"]
        or not all(node.startswith(NATIVE_PREFIX) for node in native["inventory"])
        or set(native["inventory"]).intersection(main["inventory"])
    ):
        raise ValueError("native metadata-refusal profile did not execute completely")


def pack(manifest: Path, native_manifest: Path | None, coverage: Path | None, output: Path) -> None:
    """Write an indexed artifact only after all of its mandatory commands succeeded.

    Parameters
    ----------
    manifest : Path
        Successful main pytest receipt.
    native_manifest : Path or None
        Actual mandatory native follow-up receipt, required only for index zero.
    coverage : Path or None
        Actual accumulated coverage data for the coverage interpreter.
    output : Path
        New artifact directory; existing output is refused.
    """
    main = _read(manifest)
    native = _read(native_manifest) if native_manifest else None
    _completed(main)
    _native(main, native)
    digest = None
    if coverage is not None:
        if not coverage.is_file() or coverage.is_symlink() or coverage.stat().st_size == 0:
            raise ValueError("missing ordinary accumulated coverage data")
        digest = hashlib.sha256(coverage.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    if coverage is not None:
        shutil.copyfile(coverage, output / "coverage.data")
    record = {"schema": 1, "main": main, "native": native, "coverage_sha256": digest}
    (output / "artifact.json").write_text(json.dumps(record, sort_keys=True) + "\n")


def _profile(
    records: list[tuple[Path, dict[str, Any]]], python: str, count: int
) -> list[tuple[Path, dict[str, Any]]]:
    """Require every expected index and one identical complete interpreter inventory."""
    selected = [(path, record) for path, record in records if record["main"]["python"] == python]
    if len(selected) != count or {r["main"]["index"] for _, r in selected} != set(range(count)):
        raise ValueError(f"missing or duplicate indexed shard for Python {python}")
    reference = selected[0][1]["main"]["inventory"]
    union: list[str] = []
    for _, record in selected:
        main = record["main"]
        if main["count"] != count or main["inventory"] != reference:
            raise ValueError("shard count or complete collection differs")
        union.extend(main["selected"])
    if sorted(union) != reference:
        raise ValueError("shard union omitted or duplicated tests")
    return selected


def _coverage(paths: list[Path], config: Path, output: Path, fail_under: float) -> None:
    """Combine actual data files and apply the unchanged whole-package coverage threshold."""
    output.mkdir(parents=True, exist_ok=False)
    measured = Coverage(
        data_file=str(output / "combined.coverage"), data_suffix=False, config_file=str(config)
    )
    measured.combine(data_paths=[str(path) for path in paths], strict=True, keep=True)
    measured.save()
    total = measured.report()
    # Match coverage CLI precision and its special requirement for an exact 100%.
    precision = cast("int", measured.get_option("report:precision"))
    if (fail_under == 100 and total < 100) or round(total, precision) < fail_under:
        raise ValueError(f"complete coverage {total:.2f}% is below {fail_under:g}%")
    measured.json_report(outfile=str(output / "coverage.json"))
    measured.xml_report(outfile=str(output / "coverage.xml"))


def _bound_artifact(record: dict[str, Any], expected: dict[str, str], pythons: list[str]) -> None:
    """Validate a completed main/native observation against this exact hosted invocation."""
    main = record.get("main")
    if record.get("schema") != 1 or not isinstance(main, dict):
        raise ValueError("invalid artifact schema")
    _completed(main)
    _native(main, record.get("native"))
    if (
        any(main.get(key) != value for key, value in expected.items())
        or main.get("python") not in pythons
    ):
        raise ValueError("artifact source, run, configuration or interpreter does not match")


def verify(
    root: Path,
    source: str,
    run_id: str,
    attempt: str,
    pythons: list[str],
    count: int,
    coverage_python: str,
    config: Path,
    output: Path,
    fail_under: float = 98,
) -> None:
    """Refuse incomplete, failed or foreign artifacts before measuring their full union.

    Parameters
    ----------
    root : Path
        Downloaded artifact directories containing exactly the expected shard set.
    source, run_id, attempt : str
        Required exact source SHA and current hosted run identity.
    pythons : list of str
        Complete advertised interpreter matrix.
    count : int
        Expected number of shards per interpreter.
    coverage_python : str
        Interpreter whose actual branch/subprocess coverage forms the union.
    config : Path
        Original coverage configuration, also bound to the pytest receipt.
    output : Path
        New combined data and authoritative report directory.
    fail_under : float, optional
        Original whole-package threshold; defaults to 98 percent.
    """
    paths = sorted(root.glob("*/artifact.json"))
    if len(paths) != len(pythons) * count or len(set(pythons)) != len(pythons):
        raise ValueError("missing or extra shard artifacts")
    records = [(path.parent, _read(path)) for path in paths]
    config_digest = hashlib.sha256(config.read_bytes()).hexdigest()
    expected = {
        "source": source,
        "run_id": run_id,
        "attempt": attempt,
        "config_sha256": config_digest,
    }
    for _, record in records:
        _bound_artifact(record, expected, pythons)
    coverage_paths = []
    for python in pythons:
        for directory, record in _profile(records, python, count):
            if python == coverage_python:
                path = directory / "coverage.data"
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or hashlib.sha256(path.read_bytes()).hexdigest()
                    != record.get("coverage_sha256")
                ):
                    raise ValueError("missing or altered coverage data")
                coverage_paths.append(path)
            elif record.get("coverage_sha256") is not None:
                raise ValueError("unexpected coverage interpreter")
    if coverage_python not in pythons or not 0 <= fail_under <= 100:
        raise ValueError("invalid coverage profile")
    _coverage(coverage_paths, config, output, fail_under)


def main() -> int:
    """Run the same public pack/verify commands used by hosted CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("pack")
    publish.add_argument("--manifest", type=Path, required=True)
    publish.add_argument("--native-manifest", type=Path)
    publish.add_argument("--coverage-file", type=Path)
    publish.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify")
    for name in ("root", "coverage-config", "output"):
        check.add_argument("--" + name, type=Path, required=True)
    for name in ("source", "run-id", "attempt"):
        check.add_argument("--" + name, required=True)
    check.add_argument("--python", nargs="+", required=True)
    check.add_argument("--count", type=int, required=True)
    check.add_argument("--coverage-python", default="3.12")
    check.add_argument("--fail-under", type=float, default=98)
    args = parser.parse_args()
    try:
        if args.command == "pack":
            pack(args.manifest, args.native_manifest, args.coverage_file, args.output)
        else:
            verify(
                args.root,
                args.source,
                args.run_id,
                args.attempt,
                args.python,
                args.count,
                args.coverage_python,
                args.coverage_config,
                args.output,
                args.fail_under,
            )
    except (OSError, ValueError, KeyError, TypeError, CoverageException) as error:
        print(f"CI shard refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
