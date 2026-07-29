# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — generate and verify hostile invariant conformance
"""Validate and render the six-boundary invariant-conformance registry."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 compatibility path.
    import tomli as tomllib  # pragma: no cover

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "tools" / "invariant_conformance.toml"
SPEC_PATH = "docs/coordination-spec.md"
SCHEMA_VERSION = "synapse-invariant-conformance.v1"
BOUNDARY_ORDER = (
    "single-authority",
    "immediate-effect-fencing",
    "atomic-operation-truth",
    "content-bound-global-event-identity",
    "causal-conflict-handling",
    "evidence-completeness",
)
ALLOWED_STATUSES = frozenset({"conformant", "partial", "not-implemented"})
REQUIRED_FIELDS = frozenset(
    {
        "id",
        "title",
        "status",
        "guarantee",
        "scope",
        "limitations",
        "spec_invariants",
        "sources",
        "tests",
        "evidence_modes",
    }
)
PRIVATE_PREFIXES = ("docs/internal/", ".coordination/")
TOP_LEVEL_FIELDS = frozenset({"schema_version", "generated_output", "boundary"})


class RegistryError(ValueError):
    """The conformance registry is malformed or cites invalid evidence."""


def load_registry(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load a TOML registry without importing project code."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{label} must be a non-empty string")
    return value


def _require_text_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RegistryError(
            f"{label} must be a {'possibly empty' if allow_empty else 'non-empty'} list"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_text(item, f"{label}[{index}]"))
    if len(result) != len(set(result)):
        raise RegistryError(f"{label} contains duplicates")
    return result


def _validate_evidence_path(root: Path, value: str, *, test: bool) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RegistryError(f"evidence path must be repository-relative: {value}")
    if value.startswith(PRIVATE_PREFIXES):
        raise RegistryError(f"public registry cannot cite private evidence: {value}")
    if test and (not value.startswith("tests/test_") or path.suffix != ".py"):
        raise RegistryError(f"test evidence must be a tests/test_*.py file: {value}")
    if not test and (not value.startswith("src/synapse_channel/") or path.suffix != ".py"):
        raise RegistryError(f"source evidence must be a src/synapse_channel/*.py file: {value}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RegistryError(f"evidence path escapes the repository: {value}") from exc
    if not resolved.is_file():
        raise RegistryError(f"evidence path does not exist as a file: {value}")


def validate_registry(raw: dict[str, Any], root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    """Return normalized boundary rows after strict schema and evidence checks."""
    if set(raw) != TOP_LEVEL_FIELDS:
        top_missing = sorted(TOP_LEVEL_FIELDS - set(raw))
        top_unknown = sorted(set(raw) - TOP_LEVEL_FIELDS)
        raise RegistryError(
            f"top-level fields differ: missing={top_missing!r}, unknown={top_unknown!r}"
        )
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError(f"schema_version must be {SCHEMA_VERSION}")
    output = _require_text(raw.get("generated_output"), "generated_output")
    if (
        output.startswith(PRIVATE_PREFIXES)
        or Path(output).is_absolute()
        or ".." in Path(output).parts
    ):
        raise RegistryError("generated_output must be a public repository-relative path")
    rows = raw.get("boundary")
    if not isinstance(rows, list):
        raise RegistryError("boundary must be a list")
    identifiers = tuple(row.get("id") if isinstance(row, dict) else None for row in rows)
    if identifiers != BOUNDARY_ORDER:
        raise RegistryError(f"boundary ids must appear exactly once in order: {BOUNDARY_ORDER!r}")

    spec_text = (root / SPEC_PATH).read_text(encoding="utf-8")
    spec_ids = set(re.findall(r"^### (INV-[A-Z]+-[0-9]+)\s", spec_text, flags=re.MULTILINE))
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            raise RegistryError(f"boundary[{index}] must be a table")
        unknown = set(item) - REQUIRED_FIELDS
        missing = REQUIRED_FIELDS - set(item)
        if unknown or missing:
            detail = f"missing={sorted(missing)!r}, unknown={sorted(unknown)!r}"
            raise RegistryError(f"boundary[{index}] fields differ: {detail}")
        row: dict[str, Any] = {
            key: _require_text(item[key], f"boundary[{index}].{key}")
            for key in ("id", "title", "status", "guarantee", "scope")
        }
        if row["status"] not in ALLOWED_STATUSES:
            raise RegistryError(f"boundary[{index}].status is unknown: {row['status']}")
        limitations = _require_text_list(
            item["limitations"], f"boundary[{index}].limitations", allow_empty=True
        )
        if row["status"] != "conformant" and not limitations:
            raise RegistryError(f"boundary[{index}] must state limitations unless conformant")
        if row["status"] == "conformant" and limitations:
            raise RegistryError(f"boundary[{index}] cannot be conformant while listing limitations")
        invariants = _require_text_list(
            item["spec_invariants"], f"boundary[{index}].spec_invariants"
        )
        missing_invariants = sorted(set(invariants) - spec_ids)
        if missing_invariants:
            raise RegistryError(
                f"boundary[{index}] cites unknown spec invariants: {missing_invariants!r}"
            )
        sources = _require_text_list(item["sources"], f"boundary[{index}].sources")
        tests = _require_text_list(item["tests"], f"boundary[{index}].tests")
        modes = _require_text_list(item["evidence_modes"], f"boundary[{index}].evidence_modes")
        for source in sources:
            _validate_evidence_path(root, source, test=False)
        for test_path in tests:
            _validate_evidence_path(root, test_path, test=True)
        row.update(
            limitations=limitations,
            spec_invariants=invariants,
            sources=sources,
            tests=tests,
            evidence_modes=modes,
        )
        normalized.append(row)
    return normalized


def render_registry(raw: dict[str, Any], root: Path = REPO_ROOT) -> bytes:
    """Render stable public JSON after validating the canonical TOML."""
    rows = validate_registry(raw, root)
    counts = {
        status: sum(row["status"] == status for row in rows) for status in sorted(ALLOWED_STATUSES)
    }
    document = {
        "schema_version": SCHEMA_VERSION,
        "overall_status": "conformant" if counts["conformant"] == len(rows) else "partial",
        "summary": {"total": len(rows), **counts},
        "boundaries": rows,
    }
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def output_path(raw: dict[str, Any], root: Path = REPO_ROOT) -> Path:
    """Resolve the validated generated output path."""
    value = _require_text(raw.get("generated_output"), "generated_output")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value.startswith(PRIVATE_PREFIXES):
        raise RegistryError("generated_output must be a public repository-relative path")
    return root / path


def update_registry(raw: dict[str, Any], root: Path = REPO_ROOT) -> None:
    """Write the generated registry atomically."""
    rendered = render_registry(raw, root)
    target = output_path(raw, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_registry(raw: dict[str, Any], root: Path = REPO_ROOT) -> None:
    """Fail if the generated JSON does not exactly match canonical input."""
    expected = render_registry(raw, root)
    target = output_path(raw, root)
    if not target.is_file() or target.read_bytes() != expected:
        raise RegistryError(
            f"{target.relative_to(root)} is stale; run tools/invariant_conformance.py --update"
        )


def enforce_registry(raw: dict[str, Any], root: Path = REPO_ROOT) -> None:
    """Fail until all six boundaries truthfully report conformance."""
    check_registry(raw, root)
    incomplete = [row for row in validate_registry(raw, root) if row["status"] != "conformant"]
    if incomplete:
        details = ", ".join(f"{row['id']}={row['status']}" for row in incomplete)
        raise RegistryError(f"invariant programme is not fully conformant: {details}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--update", action="store_true", help="regenerate the public JSON")
    mode.add_argument("--check", action="store_true", help="verify the public JSON is current")
    mode.add_argument(
        "--enforce", action="store_true", help="require all six boundaries to conform"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the selected registry operation."""
    args = build_parser().parse_args(argv)
    try:
        raw = load_registry()
        if args.update:
            update_registry(raw)
        elif args.enforce:
            enforce_registry(raw)
        else:
            check_registry(raw)
    except (OSError, RegistryError, tomllib.TOMLDecodeError) as exc:
        print(f"invariant-conformance: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
