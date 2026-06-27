#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — semantic claim selector resolver
"""Resolve semantic claim selectors into ordinary file-scope claim paths.

The hub remains path-scope only. This tool is the local semantic layer that lets
agents ask for a module, public symbol, API surface, test owner, generated
artefact, migration, or source path and receive deterministic ``--paths`` values
for ``synapse git-claim`` plus receipt-ready JSON.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import generated_dependency_claims, test_ownership_map  # noqa: E402

SEMANTIC_SELECTOR_KINDS = frozenset(
    {"module", "symbol", "api", "source", "test", "generated", "migration"}
)
"""Supported selector kinds for semantic claim resolution."""


@dataclass(frozen=True)
class ParsedSelector:
    """One parsed ``kind:value`` selector from the command line."""

    raw: str
    kind: str
    value: str


@dataclass(frozen=True)
class SemanticClaimRecord:
    """Resolved semantic selector and its derived claim paths."""

    selector: str
    kind: str
    value: str
    sources: tuple[str, ...]
    modules: tuple[str, ...]
    symbols: tuple[str, ...]
    tests: tuple[str, ...]
    generated: tuple[str, ...]
    claim_paths: tuple[str, ...]


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for semantic claim resolution."""

    repo_root: Path
    selectors: tuple[str, ...]
    json_output: bool
    claim_args: bool
    check: bool


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    """Return values without duplicates while preserving first-seen order."""
    return tuple(dict.fromkeys(values))


def _repo_relative(path: Path, repo_root: Path) -> str:
    """Return a user path as a repository-relative POSIX path when possible."""
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def parse_selector(raw: str) -> ParsedSelector:
    """Parse one semantic selector in ``kind:value`` form."""
    if ":" not in raw:
        msg = f"selector must use kind:value: {raw}"
        raise ValueError(msg)
    kind, value = raw.split(":", maxsplit=1)
    kind = kind.strip()
    value = value.strip()
    if kind not in SEMANTIC_SELECTOR_KINDS:
        msg = f"unsupported semantic selector kind: {raw}"
        raise ValueError(msg)
    if not value:
        msg = f"selector value must not be empty: {raw}"
        raise ValueError(msg)
    return ParsedSelector(raw=raw, kind=kind, value=value)


def _index_by_source(
    records: Sequence[test_ownership_map.OwnershipRecord],
) -> dict[str, test_ownership_map.OwnershipRecord]:
    """Index ownership records by source path."""
    return {record.source: record for record in records}


def _index_by_module(
    records: Sequence[test_ownership_map.OwnershipRecord],
) -> dict[str, test_ownership_map.OwnershipRecord]:
    """Index ownership records by importable module name."""
    return {record.module: record for record in records}


def _normalise_generated_for_sources(
    repo_root: Path,
    sources: Sequence[str],
) -> tuple[str, ...]:
    """Return generated outputs that can be stale after changing ``sources``."""
    generated_records = generated_dependency_claims.build_dependency_map(repo_root)
    selected, _unknown = generated_dependency_claims.select_records(
        generated_records,
        tuple(Path(source) for source in sources),
        (),
        repo_root,
    )
    return tuple(record.generated for record in selected)


def _record_from_owner(
    selector: ParsedSelector,
    owner: test_ownership_map.OwnershipRecord,
    symbols: Sequence[str],
    repo_root: Path,
) -> SemanticClaimRecord:
    """Build a semantic claim record from a source ownership record."""
    sources = (owner.source,)
    tests = tuple(test_owner.path for test_owner in owner.test_owners)
    generated = _normalise_generated_for_sources(repo_root, sources)
    return SemanticClaimRecord(
        selector=selector.raw,
        kind=selector.kind,
        value=selector.value,
        sources=sources,
        modules=(owner.module,),
        symbols=tuple(symbols),
        tests=tests,
        generated=generated,
        claim_paths=_unique_ordered((*sources, *tests, *generated)),
    )


def _resolve_symbol_like(
    selector: ParsedSelector,
    module_index: dict[str, test_ownership_map.OwnershipRecord],
    repo_root: Path,
) -> SemanticClaimRecord:
    """Resolve a fully qualified public symbol or API selector."""
    module_name, separator, symbol = selector.value.rpartition(".")
    if not separator:
        msg = f"unknown {selector.kind} selector: {selector.raw}"
        raise ValueError(msg)
    owner = module_index.get(module_name)
    if owner is None or symbol not in owner.symbols:
        msg = f"unknown {selector.kind} selector: {selector.raw}"
        raise ValueError(msg)
    return _record_from_owner(selector, owner, (symbol,), repo_root)


def _resolve_module(
    selector: ParsedSelector,
    module_index: dict[str, test_ownership_map.OwnershipRecord],
    repo_root: Path,
) -> SemanticClaimRecord:
    """Resolve an importable module selector."""
    owner = module_index.get(selector.value)
    if owner is None:
        msg = f"unknown module selector: {selector.raw}"
        raise ValueError(msg)
    return _record_from_owner(selector, owner, owner.symbols, repo_root)


def _resolve_source(
    selector: ParsedSelector,
    source_index: dict[str, test_ownership_map.OwnershipRecord],
    repo_root: Path,
) -> SemanticClaimRecord:
    """Resolve a source path selector."""
    source = _repo_relative(Path(selector.value), repo_root)
    owner = source_index.get(source)
    if owner is None:
        msg = f"unknown source selector: {selector.raw}"
        raise ValueError(msg)
    normalised_selector = ParsedSelector(selector.raw, selector.kind, source)
    return _record_from_owner(normalised_selector, owner, owner.symbols, repo_root)


def _resolve_test(
    selector: ParsedSelector,
    ownership_records: Sequence[test_ownership_map.OwnershipRecord],
    repo_root: Path,
) -> SemanticClaimRecord:
    """Resolve a test path selector to the source modules it owns."""
    test_path = _repo_relative(Path(selector.value), repo_root)
    owners = tuple(
        record
        for record in ownership_records
        if any(test_owner.path == test_path for test_owner in record.test_owners)
    )
    if not owners:
        msg = f"unknown test selector: {selector.raw}"
        raise ValueError(msg)

    sources = tuple(record.source for record in owners)
    generated = _normalise_generated_for_sources(repo_root, sources)
    return SemanticClaimRecord(
        selector=selector.raw,
        kind=selector.kind,
        value=test_path,
        sources=sources,
        modules=tuple(record.module for record in owners),
        symbols=tuple(symbol for record in owners for symbol in record.symbols),
        tests=(test_path,),
        generated=generated,
        claim_paths=_unique_ordered((*sources, test_path, *generated)),
    )


def _resolve_generated(
    selector: ParsedSelector,
    repo_root: Path,
) -> SemanticClaimRecord:
    """Resolve a generated artefact selector."""
    generated = _repo_relative(Path(selector.value), repo_root)
    records = generated_dependency_claims.build_dependency_map(repo_root)
    selected, unknown = generated_dependency_claims.select_records(
        records,
        (),
        (Path(generated),),
        repo_root,
    )
    if unknown or not selected:
        msg = f"unknown generated selector: {selector.raw}"
        raise ValueError(msg)
    generated_paths = tuple(record.generated for record in selected)
    return SemanticClaimRecord(
        selector=selector.raw,
        kind=selector.kind,
        value=generated,
        sources=(),
        modules=(),
        symbols=(),
        tests=(),
        generated=generated_paths,
        claim_paths=generated_paths,
    )


def _resolve_migration(selector: ParsedSelector, repo_root: Path) -> SemanticClaimRecord:
    """Resolve a migration path selector."""
    migration = _repo_relative(Path(selector.value), repo_root)
    if not (repo_root / migration).exists():
        msg = f"unknown migration selector: {selector.raw}"
        raise ValueError(msg)
    return SemanticClaimRecord(
        selector=selector.raw,
        kind=selector.kind,
        value=migration,
        sources=(),
        modules=(),
        symbols=(),
        tests=(),
        generated=(),
        claim_paths=(migration,),
    )


def resolve_selectors(
    repo_root: Path,
    selectors: Sequence[str],
) -> tuple[SemanticClaimRecord, ...]:
    """Resolve semantic selectors into claim records for ``repo_root``."""
    ownership_records = test_ownership_map.build_ownership_map(repo_root)
    source_index = _index_by_source(ownership_records)
    module_index = _index_by_module(ownership_records)
    resolved: list[SemanticClaimRecord] = []

    for raw in selectors:
        selector = parse_selector(raw)
        if selector.kind == "module":
            resolved.append(_resolve_module(selector, module_index, repo_root))
        elif selector.kind in {"symbol", "api"}:
            resolved.append(_resolve_symbol_like(selector, module_index, repo_root))
        elif selector.kind == "source":
            resolved.append(_resolve_source(selector, source_index, repo_root))
        elif selector.kind == "test":
            resolved.append(_resolve_test(selector, ownership_records, repo_root))
        elif selector.kind == "generated":
            resolved.append(_resolve_generated(selector, repo_root))
        else:
            resolved.append(_resolve_migration(selector, repo_root))

    return tuple(resolved)


def records_to_json(records: Sequence[SemanticClaimRecord]) -> list[dict[str, object]]:
    """Convert semantic claim records into a stable JSON payload."""
    return [
        {
            "selector": record.selector,
            "kind": record.kind,
            "value": record.value,
            "sources": list(record.sources),
            "modules": list(record.modules),
            "symbols": list(record.symbols),
            "tests": list(record.tests),
            "generated": list(record.generated),
            "claim_paths": list(record.claim_paths),
        }
        for record in records
    ]


def render_human(records: Sequence[SemanticClaimRecord]) -> str:
    """Render semantic claim records as compact human-readable text."""
    return "\n".join(f"{record.selector} -> {', '.join(record.claim_paths)}" for record in records)


def render_claim_args(records: Sequence[SemanticClaimRecord]) -> str:
    """Render unique claim paths as ``synapse git-claim`` path arguments."""
    paths = _unique_ordered(path for record in records for path in record.claim_paths)
    parts: list[str] = []
    for path in paths:
        parts.extend(("--paths", path))
    return " ".join(shlex.quote(part) for part in parts)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments for semantic claim resolution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to the current checkout.",
    )
    parser.add_argument(
        "--selector",
        action="append",
        default=[],
        help=(
            "Semantic selector in kind:value form. Supported kinds: "
            "module, symbol, api, source, test, generated, migration."
        ),
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
        help="Validate selectors and print a pass diagnostic.",
    )
    namespace = parser.parse_args(argv)
    return CliArgs(
        repo_root=namespace.repo_root,
        selectors=tuple(namespace.selector),
        json_output=bool(namespace.json),
        claim_args=bool(namespace.claim_args),
        check=bool(namespace.check),
    )


def _claim_path_count(records: Sequence[SemanticClaimRecord]) -> int:
    """Return the number of unique claim paths across ``records``."""
    return len(_unique_ordered(path for record in records for path in record.claim_paths))


def main(argv: Sequence[str] | None = None) -> int:
    """Run semantic claim resolution and return a process exit code."""
    args = parse_args(argv)
    if not args.selectors:
        print("at least one --selector is required", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    try:
        records = resolve_selectors(repo_root, args.selectors)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.claim_args:
        print(render_claim_args(records))
    elif args.json_output:
        print(json.dumps(records_to_json(records), indent=2, sort_keys=True))
    else:
        rendered = render_human(records)
        if rendered:
            print(rendered)

    if args.check:
        print(
            "semantic claim resolution passed: "
            f"{len(records)} selector(s), {_claim_path_count(records)} claim path(s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
