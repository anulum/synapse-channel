#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — generated-output dependency claim mapper
"""Map generated repository outputs back to the inputs that can stale them.

The map is intentionally static and dependency-free. It gives agents a
deterministic way to include generated artefacts in a file-scope claim when they
touch source paths that feed those artefacts. It does not regenerate files and
does not certify that generated content is fresh; use the owning generator's
``--check`` command for freshness validation.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class GeneratedRule:
    """Configured generated output and the inputs that can stale it."""

    generated: str
    dependencies: tuple[str, ...]
    required_dependencies: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class DependencyRecord:
    """One generated output with repository-relative dependency patterns."""

    generated: str
    dependencies: tuple[str, ...]
    required_dependencies: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line options for the generated dependency mapper."""

    repo_root: Path
    source: tuple[Path, ...]
    generated: tuple[Path, ...]
    json_output: bool
    claim_args: bool
    check: bool


CAPABILITY_DEPENDENCIES = (
    "tools/capability_manifest.py",
    "tools/capability_manifest.toml",
    "pyproject.toml",
    "src/synapse_channel/*.py",
    "src/synapse_channel/**/*.py",
    "tests/test_*.py",
    "benchmarks/*.py",
    "docs/*.md",
    ".github/workflows/*.yml",
)
"""Repository inputs that feed the capability inventory surfaces."""

CAPABILITY_REQUIRED_DEPENDENCIES = (
    "tools/capability_manifest.py",
    "tools/capability_manifest.toml",
    "src/synapse_channel/*.py",
    "src/synapse_channel/**/*.py",
)
"""Minimum dependency patterns that must exist for the capability map."""

DEFAULT_RULES = (
    GeneratedRule(
        generated="README.md",
        dependencies=CAPABILITY_DEPENDENCIES,
        required_dependencies=CAPABILITY_REQUIRED_DEPENDENCIES,
        description="README capability inventory snapshot and generated counts",
    ),
    GeneratedRule(
        generated="docs/_generated/capability_manifest.json",
        dependencies=CAPABILITY_DEPENDENCIES,
        required_dependencies=CAPABILITY_REQUIRED_DEPENDENCIES,
        description="machine-readable capability manifest generated from the checkout",
    ),
)
"""Generated-output dependency rules known to this repository."""


def _normalise_requested_path(path: Path, repo_root: Path) -> str:
    """Return a user-supplied path as a repository-relative POSIX path."""
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _path_exists(path: str, repo_root: Path) -> bool:
    """Return whether ``path`` exists below ``repo_root``."""
    return (repo_root / path).exists()


def _pattern_matches_path(pattern: str, path: str) -> bool:
    """Return whether a dependency pattern covers one repository path."""
    return pattern == path or fnmatch.fnmatchcase(path, pattern)


def _pattern_has_match(pattern: str, repo_root: Path) -> bool:
    """Return whether a dependency pattern matches at least one path in the repo."""
    if any(marker in pattern for marker in ("*", "?", "[")):
        return any((repo_root / ".").glob(pattern))
    return (repo_root / pattern).exists()


def build_dependency_map(
    repo_root: Path = REPO_ROOT,
    rules: Sequence[GeneratedRule] = DEFAULT_RULES,
) -> tuple[DependencyRecord, ...]:
    """Build generated-output dependency records for ``repo_root``."""
    _ = repo_root
    return tuple(
        DependencyRecord(
            generated=rule.generated,
            dependencies=rule.dependencies,
            required_dependencies=rule.required_dependencies,
            description=rule.description,
        )
        for rule in rules
    )


def _matches_source(record: DependencyRecord, source: str) -> bool:
    """Return whether ``source`` can stale ``record``."""
    return any(_pattern_matches_path(pattern, source) for pattern in record.dependencies)


def _select_by_sources(
    records: Sequence[DependencyRecord],
    requested_sources: Sequence[Path],
    repo_root: Path,
) -> tuple[tuple[DependencyRecord, ...], tuple[str, ...]]:
    """Filter records by source paths and return unknown source paths."""
    selected: dict[str, DependencyRecord] = {}
    unknown: list[str] = []
    for requested in requested_sources:
        source = _normalise_requested_path(requested, repo_root)
        matches = [
            record
            for record in records
            if _path_exists(source, repo_root) and _matches_source(record, source)
        ]
        if matches:
            selected.update({record.generated: record for record in matches})
        else:
            unknown.append(source)
    return tuple(selected[key] for key in sorted(selected)), tuple(unknown)


def _select_by_generated(
    records: Sequence[DependencyRecord],
    requested_generated: Sequence[Path],
    repo_root: Path,
) -> tuple[tuple[DependencyRecord, ...], tuple[str, ...]]:
    """Filter records by generated output path and return unknown outputs."""
    by_generated = {record.generated: record for record in records}
    selected: dict[str, DependencyRecord] = {}
    unknown: list[str] = []
    for requested in requested_generated:
        generated = _normalise_requested_path(requested, repo_root)
        record = by_generated.get(generated)
        if record is None:
            unknown.append(generated)
        else:
            selected[record.generated] = record
    return tuple(selected[key] for key in sorted(selected)), tuple(unknown)


def select_records(
    records: Sequence[DependencyRecord],
    requested_sources: Sequence[Path],
    requested_generated: Sequence[Path],
    repo_root: Path,
) -> tuple[tuple[DependencyRecord, ...], tuple[str, ...]]:
    """Select dependency records using optional source and generated filters."""
    selected: tuple[DependencyRecord, ...] = tuple(records)
    unknown: list[str] = []

    if requested_sources:
        selected, source_unknown = _select_by_sources(selected, requested_sources, repo_root)
        unknown.extend(f"source:{path}" for path in source_unknown)

    if requested_generated:
        selected, generated_unknown = _select_by_generated(
            selected,
            requested_generated,
            repo_root,
        )
        unknown.extend(f"generated:{path}" for path in generated_unknown)

    return selected, tuple(unknown)


def check_records(records: Sequence[DependencyRecord], repo_root: Path) -> tuple[str, ...]:
    """Return integrity problems in the generated dependency map."""
    problems: list[str] = []
    for record in records:
        if not (repo_root / record.generated).exists():
            problems.append(f"missing generated output: {record.generated}")
        for pattern in record.required_dependencies:
            if not _pattern_has_match(pattern, repo_root):
                problems.append(f"dependency pattern matched no files: {pattern}")
    return tuple(dict.fromkeys(problems))


def records_to_json(records: Sequence[DependencyRecord]) -> list[dict[str, object]]:
    """Convert dependency records into a stable JSON payload."""
    return [
        {
            "generated": record.generated,
            "description": record.description,
            "dependencies": list(record.dependencies),
            "required_dependencies": list(record.required_dependencies),
        }
        for record in records
    ]


def render_human(records: Sequence[DependencyRecord]) -> str:
    """Render dependency records as compact human-readable text."""
    lines: list[str] = []
    for record in records:
        lines.append(f"{record.generated} <- {', '.join(record.dependencies)}")
    return "\n".join(lines)


def render_claim_args(records: Sequence[DependencyRecord]) -> str:
    """Render selected generated outputs as ``synapse git-claim`` path args."""
    parts: list[str] = []
    for record in records:
        parts.extend(("--paths", record.generated))
    return " ".join(shlex.quote(part) for part in parts)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments for the dependency mapper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to the current checkout.",
    )
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        default=[],
        help="Limit output to generated files staleable by this source path; repeatable.",
    )
    parser.add_argument(
        "--generated",
        action="append",
        type=Path,
        default=[],
        help="Limit output to one generated output path; repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--claim-args",
        action="store_true",
        help="Emit only --paths arguments for synapse git-claim.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that configured generated outputs and required inputs exist.",
    )
    namespace = parser.parse_args(argv)
    return CliArgs(
        repo_root=namespace.repo_root,
        source=tuple(namespace.source),
        generated=tuple(namespace.generated),
        json_output=bool(namespace.json),
        claim_args=bool(namespace.claim_args),
        check=bool(namespace.check),
    )


def _report_unknown(unknown: Sequence[str]) -> int:
    """Print unknown selector diagnostics and return a CLI error code."""
    for entry in unknown:
        kind, path = entry.split(":", maxsplit=1)
        if kind == "source":
            print(f"source path matches no generated dependency rule: {path}", file=sys.stderr)
        else:
            print(f"unknown generated output: {path}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the generated dependency mapper and return a process exit code."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    records = build_dependency_map(repo_root)
    selected_records, unknown = select_records(records, args.source, args.generated, repo_root)
    if unknown:
        return _report_unknown(unknown)

    if args.claim_args:
        print(render_claim_args(selected_records))
    elif args.json_output:
        print(json.dumps(records_to_json(selected_records), indent=2, sort_keys=True))
    else:
        rendered = render_human(selected_records)
        if rendered:
            print(rendered)

    if args.check:
        problems = check_records(records, repo_root)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"generated dependency map passed: {len(records)} generated output(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
