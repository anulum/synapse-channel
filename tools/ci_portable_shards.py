#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — platform-bound complete portable-suite artifacts
"""Certify complete portable pytest execution without claiming Linux native coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.pytest_shard_schedule import (
    load_shard_timings,
    partition_test_modules,
    validate_module_durations,
)

PROFILE = "portable-full-suite-v1"


def _read(path: Path) -> dict[str, Any]:
    """Load an ordinary artifact object, refusing missing files and symlinks."""
    if not path.is_file() or path.is_symlink():
        raise ValueError("missing ordinary portable shard artifact")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("portable artifact must be an object")
    return cast("dict[str, Any]", value)


def _ids(record: dict[str, Any], key: str) -> list[str]:
    """Require unique sorted nonempty node IDs; an empty outcome subset is valid."""
    value = record.get(key)
    if (
        not isinstance(value, list)
        or not all(isinstance(node, str) and node for node in value)
        or value != sorted(set(value))
    ):
        raise ValueError(f"invalid portable {key} inventory")
    return cast("list[str]", value)


def _coordinates(record: dict[str, Any]) -> tuple[int, int]:
    """Validate worker coordinates before recomputing its actual partition."""
    index, count = record.get("index"), record.get("count")
    if (
        type(index) is not int
        or type(count) is not int
        or not 1 <= count <= 128
        or not 0 <= index < count
    ):
        raise ValueError("invalid portable shard coordinates")
    return index, count


def _completed(record: dict[str, Any], platform: str, timings: Path) -> None:
    """Require genuine completed execution and the exact shared module schedule."""
    index, count = _coordinates(record)
    inventory, selected = _ids(record, "inventory"), _ids(record, "selected")
    digest, weights = load_shard_timings(timings, record["python"])
    expected = list(partition_test_modules(inventory, count, weights)[index])
    if (
        type(record.get("schema")) is not int
        or record["schema"] != 1
        or record.get("platform") != platform
        or record.get("success") is not True
        or record.get("exitstatus") != 0
        or not selected
        or selected != expected
        or _ids(record, "completed") != selected
        or not set(_ids(record, "called")).issubset(selected)
        or not set(_ids(record, "skipped")).issubset(selected)
        or set(record["called"]) | set(record["skipped"]) != set(selected)
    ):
        raise ValueError("incomplete, foreign or misidentified portable shard")
    if record.get("schedule") != {
        "algorithm": "module-lpt-v1",
        "timings_sha256": digest,
        "weights": weights,
    }:
        raise ValueError("portable shard used a different shared timing snapshot")
    durations = validate_module_durations(record["module_durations"])
    if set(durations) != {node.split("::", 1)[0] for node in selected}:
        raise ValueError("portable durations do not describe the executed modules")


def pack(manifest: Path, timings: Path, output: Path) -> None:
    """Publish a successful portable receipt bound to this runner's actual platform.

    Parameters
    ----------
    manifest : Path
        Receipt emitted by the actual indexed pytest invocation.
    timings : Path
        Immutable scheduling input selected once for this workflow run.
    output : Path
        New artifact directory. Existing destinations are refused.
    """
    record = _read(manifest)
    _completed(record, sys.platform, timings)
    output.mkdir(parents=True, exist_ok=False)
    (output / "artifact.json").write_text(
        json.dumps({"schema": 1, "profile": PROFILE, "main": record}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _bound(artifact: dict[str, Any], expected: dict[str, str], count: int) -> dict[str, Any]:
    """Require the complete portable profile and exact run/configuration identity."""
    if (
        set(artifact) != {"schema", "profile", "main"}
        or type(artifact["schema"]) is not int
        or artifact["schema"] != 1
        or artifact["profile"] != PROFILE
        or not isinstance(artifact["main"], dict)
    ):
        raise ValueError("invalid portable artifact profile")
    record = cast("dict[str, Any]", artifact["main"])
    _, actual_count = _coordinates(record)
    if actual_count != count or any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("portable artifact source, run, platform or configuration differs")
    return record


def _attempt(value: object) -> int:
    """Require an ordinary canonical positive hosted-attempt string."""
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 10
    ):
        raise ValueError("invalid portable hosted attempt")
    return int(value)


def _union(records: list[dict[str, Any]], count: int) -> None:
    """Check every actual index and independent full inventory union."""
    if len(records) != count or {record["index"] for record in records} != set(range(count)):
        raise ValueError("missing or duplicate portable shard index")
    reference = _ids(records[0], "inventory")
    selected = []
    for record in records:
        if _ids(record, "inventory") != reference:
            raise ValueError("portable complete collections differ")
        selected.extend(_ids(record, "selected"))
    if sorted(selected) != reference:
        raise ValueError("portable shard union omitted or duplicated tests")


def verify(
    root: Path,
    source: str,
    run_id: str,
    attempt: str,
    platform: str,
    python: str,
    count: int,
    config: Path,
    timings: Path,
    output: Path,
    *,
    allow_earlier_attempts: bool = False,
) -> None:
    """Require every complete platform-bound shard before publishing observed timings.

    Parameters
    ----------
    root : Path
        Downloaded immutable artifact directories for this invocation.
    source, run_id, attempt : str
        Exact checked-out source, hosted run identity and aggregate attempt.
    platform, python : str
        Required actual operating-system identifier and interpreter major/minor.
    count : int
        Complete expected worker population.
    config : Path
        Original pytest configuration whose digest every receipt must match.
    timings : Path
        Common immutable scheduling input used by every worker.
    output : Path
        New result directory; no coverage certification is produced.
    allow_earlier_attempts : bool
        Accept successful producer attempts from this same run up to the current
        aggregate attempt. Retained source, platform, configuration, schedule and
        complete execution evidence must still match. Default requires exact attempt.
    """
    current_attempt = _attempt(attempt)
    if type(count) is not int or not 1 <= count <= 128:
        raise ValueError("invalid expected portable shard count")
    expected = dict(
        source=source,
        run_id=run_id,
        attempt=attempt,
        platform=platform,
        python=python,
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
    )
    if allow_earlier_attempts:
        expected.pop("attempt")
    records = [
        _bound(_read(path), expected, count) for path in sorted(root.glob("*/artifact.json"))
    ]
    for record in records:
        producer_attempt = _attempt(record.get("attempt"))
        if producer_attempt > current_attempt:
            raise ValueError("portable producer attempt is newer than the aggregate")
    _union(records, count)
    observed: dict[str, float] = {}
    for record in records:
        _completed(record, platform, timings)
        observed.update(validate_module_durations(record["module_durations"]))
    output.mkdir(parents=True, exist_ok=False)
    (output / "timings.json").write_text(
        json.dumps({"schema": 1, "profiles": {python: observed}}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """Run the public portable pack/verify commands used by the cross-OS workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("pack")
    for name in ("manifest", "timings", "output"):
        publish.add_argument("--" + name, type=Path, required=True)
    check = commands.add_parser("verify")
    for name in ("root", "config", "timings", "output"):
        check.add_argument("--" + name, type=Path, required=True)
    for name in ("source", "run-id", "attempt", "python"):
        check.add_argument("--" + name, required=True)
    check.add_argument("--platform", choices=("darwin", "linux", "win32"), required=True)
    check.add_argument("--count", type=int, required=True)
    check.add_argument("--allow-earlier-attempts", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "pack":
            pack(args.manifest, args.timings, args.output)
        else:
            verify(
                args.root,
                args.source,
                args.run_id,
                args.attempt,
                args.platform,
                args.python,
                args.count,
                args.config,
                args.timings,
                args.output,
                allow_earlier_attempts=args.allow_earlier_attempts,
            )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Portable CI shard refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
