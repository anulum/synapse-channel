#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — map source files to likely owning tests
"""Build a deterministic map from source modules to likely owning tests.

The mapper uses Python's AST rather than text matching. Test imports provide
the primary evidence. A conservative filename fallback handles common local
patterns such as ``tests/test_cli_locking_release.py`` owning
``src/synapse_channel/cli_locking.py`` when the test exercises a CLI through the
top-level parser instead of importing the focused module directly.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path.cwd()
PACKAGE_NAME = "synapse_channel"
DEFAULT_SOURCE_ROOT = Path("src") / PACKAGE_NAME
DEFAULT_TESTS_ROOT = Path("tests")


@dataclass(frozen=True)
class SourceModule:
    """One Python source module discovered under ``src/synapse_channel``."""

    source: str
    module: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ImportedModule:
    """One package import discovered in a test file."""

    module: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class TestOwner:
    """One test file mapped to a source file with evidence for the mapping."""

    path: str
    reasons: tuple[str, ...]
    imported_symbols: tuple[str, ...]


@dataclass(frozen=True)
class OwnershipRecord:
    """One source file and the tests likely responsible for exercising it."""

    source: str
    module: str
    symbols: tuple[str, ...]
    test_owners: tuple[TestOwner, ...]


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the ownership mapper."""

    repo_root: Path
    source: tuple[Path, ...]
    require_owned: tuple[Path, ...]
    json_output: bool
    check: bool


@dataclass(frozen=True)
class PendingOwner:
    """Mutable-construction payload before owner evidence is normalised."""

    reasons: frozenset[str]
    imported_symbols: frozenset[str]


def _relative(path: Path, root: Path) -> str:
    """Return ``path`` as a POSIX path relative to ``root``."""
    return path.relative_to(root).as_posix()


def _module_name(source_path: Path, source_root: Path) -> str:
    """Return the importable module name for one source file."""
    relative = source_path.relative_to(source_root).with_suffix("")
    return ".".join((PACKAGE_NAME, *relative.parts))


def _parse_python(path: Path) -> ast.Module:
    """Parse a Python file into an AST."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def defined_symbols(path: Path) -> tuple[str, ...]:
    """Return public top-level functions and classes defined in ``path``."""
    tree = _parse_python(path)
    names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    return tuple(sorted(names))


def discover_sources(
    repo_root: Path, source_root: Path = DEFAULT_SOURCE_ROOT
) -> tuple[SourceModule, ...]:
    """Discover source modules that can be mapped to tests."""
    absolute_source_root = repo_root / source_root
    if not absolute_source_root.exists():
        return ()

    sources: list[SourceModule] = []
    for path in sorted(absolute_source_root.rglob("*.py")):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        sources.append(
            SourceModule(
                source=_relative(path, repo_root),
                module=_module_name(path, absolute_source_root),
                symbols=defined_symbols(path),
            )
        )
    return tuple(sources)


def discover_tests(repo_root: Path, tests_root: Path = DEFAULT_TESTS_ROOT) -> tuple[Path, ...]:
    """Discover top-level test modules."""
    absolute_tests_root = repo_root / tests_root
    if not absolute_tests_root.exists():
        return ()
    return tuple(sorted(absolute_tests_root.glob("test_*.py")))


def imported_modules(test_path: Path) -> tuple[ImportedModule, ...]:
    """Return package imports from one test file."""
    imports: dict[str, set[str]] = {}
    tree = _parse_python(test_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE_NAME or alias.name.startswith(f"{PACKAGE_NAME}."):
                    imports.setdefault(alias.name, set())
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            if node.module == PACKAGE_NAME or node.module.startswith(f"{PACKAGE_NAME}."):
                symbols = {alias.name for alias in node.names if alias.name != "*"}
                imports.setdefault(node.module, set()).update(symbols)
    return tuple(
        ImportedModule(module=module, symbols=tuple(sorted(symbols)))
        for module, symbols in sorted(imports.items())
    )


def _source_by_module(sources: Sequence[SourceModule]) -> dict[str, SourceModule]:
    """Index source modules by import path."""
    return {source.module: source for source in sources}


def _source_by_filename(sources: Sequence[SourceModule]) -> dict[str, tuple[SourceModule, ...]]:
    """Index source modules by filename for conservative test-name fallback."""
    indexed: dict[str, list[SourceModule]] = {}
    for source in sources:
        indexed.setdefault(Path(source.source).name, []).append(source)
    return {name: tuple(values) for name, values in indexed.items()}


def _merge_owner(
    owners: dict[str, dict[str, PendingOwner]],
    source: SourceModule,
    test_path: str,
    reason: str,
    imported_symbol_names: Iterable[str] = (),
) -> None:
    """Add one owner edge while preserving deterministic merged evidence."""
    source_owners = owners.setdefault(source.source, {})
    previous = source_owners.get(test_path)
    reasons = {reason}
    imported = set(imported_symbol_names)
    if previous is not None:
        reasons.update(previous.reasons)
        imported.update(previous.imported_symbols)
    source_owners[test_path] = PendingOwner(
        reasons=frozenset(reasons),
        imported_symbols=frozenset(imported),
    )


def _fallback_source_for_test(
    test_path: Path,
    filename_index: dict[str, tuple[SourceModule, ...]],
) -> tuple[SourceModule, str] | None:
    """Return the longest unambiguous source filename prefix for a test name."""
    stem = test_path.stem
    if not stem.startswith("test_"):
        return None

    parts = stem.removeprefix("test_").split("_")
    for length in range(len(parts), 0, -1):
        filename = f"{'_'.join(parts[:length])}.py"
        matches = filename_index.get(filename, ())
        if len(matches) == 1:
            return matches[0], f"filename fallback {test_path.name} -> {filename}"
    return None


def _owner_records(
    sources: Sequence[SourceModule],
    owners: dict[str, dict[str, PendingOwner]],
) -> tuple[OwnershipRecord, ...]:
    """Convert merged owner evidence into sorted immutable records."""
    records: list[OwnershipRecord] = []
    for source in sources:
        source_owners = owners.get(source.source, {})
        test_owners = tuple(
            TestOwner(
                path=test_path,
                reasons=tuple(sorted(pending.reasons)),
                imported_symbols=tuple(sorted(pending.imported_symbols)),
            )
            for test_path, pending in sorted(source_owners.items())
        )
        records.append(
            OwnershipRecord(
                source=source.source,
                module=source.module,
                symbols=source.symbols,
                test_owners=test_owners,
            )
        )
    return tuple(records)


def build_ownership_map(repo_root: Path = REPO_ROOT) -> tuple[OwnershipRecord, ...]:
    """Build the source-to-test ownership map for ``repo_root``."""
    sources = discover_sources(repo_root)
    module_index = _source_by_module(sources)
    filename_index = _source_by_filename(sources)
    owners: dict[str, dict[str, PendingOwner]] = {}

    for test_path in discover_tests(repo_root):
        test_relpath = _relative(test_path, repo_root)
        for imported in imported_modules(test_path):
            source = module_index.get(imported.module)
            if source is not None:
                _merge_owner(
                    owners,
                    source,
                    test_relpath,
                    f"imports {imported.module}",
                    imported.symbols,
                )

            for symbol in imported.symbols:
                nested_source = module_index.get(f"{imported.module}.{symbol}")
                if nested_source is not None:
                    _merge_owner(
                        owners,
                        nested_source,
                        test_relpath,
                        f"imports {nested_source.module}",
                    )

        fallback = _fallback_source_for_test(test_path, filename_index)
        if fallback is not None:
            source, reason = fallback
            _merge_owner(owners, source, test_relpath, reason)

    return _owner_records(sources, owners)


def _normalise_requested_source(path: Path, repo_root: Path) -> str:
    """Return a user-supplied source path as a repository-relative POSIX path."""
    if path.is_absolute():
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    return path.as_posix()


def _select_records(
    records: Sequence[OwnershipRecord],
    requested_sources: Sequence[Path],
    repo_root: Path,
) -> tuple[tuple[OwnershipRecord, ...], tuple[str, ...]]:
    """Filter records by requested source paths and report unknown paths."""
    if not requested_sources:
        return tuple(records), ()

    by_source = {record.source: record for record in records}
    selected: list[OwnershipRecord] = []
    unknown: list[str] = []
    for requested in requested_sources:
        source = _normalise_requested_source(requested, repo_root)
        record = by_source.get(source)
        if record is None:
            unknown.append(source)
        else:
            selected.append(record)
    return tuple(selected), tuple(unknown)


def _required_unowned(
    records: Sequence[OwnershipRecord],
    requested_sources: Sequence[Path],
    repo_root: Path,
) -> tuple[str, ...]:
    """Return required source paths that have no mapped test owners."""
    by_source = {record.source: record for record in records}
    unowned: list[str] = []
    for requested in requested_sources:
        source = _normalise_requested_source(requested, repo_root)
        record = by_source.get(source)
        if record is None or not record.test_owners:
            unowned.append(source)
    return tuple(unowned)


def records_to_json(records: Sequence[OwnershipRecord]) -> list[dict[str, object]]:
    """Convert ownership records into a stable JSON payload."""
    return [
        {
            "source": record.source,
            "module": record.module,
            "symbols": list(record.symbols),
            "test_owners": [
                {
                    "path": owner.path,
                    "reasons": list(owner.reasons),
                    "imported_symbols": list(owner.imported_symbols),
                }
                for owner in record.test_owners
            ],
        }
        for record in records
    ]


def render_human(records: Sequence[OwnershipRecord]) -> str:
    """Render ownership records as a compact human-readable map."""
    lines: list[str] = []
    for record in records:
        owners = ", ".join(owner.path for owner in record.test_owners)
        lines.append(f"{record.source} -> {owners or '(no mapped tests)'}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments for the ownership mapper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to scan. Defaults to the current checkout.",
    )
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        default=[],
        help="Limit output to one source path; repeatable.",
    )
    parser.add_argument(
        "--require-owned",
        action="append",
        type=Path,
        default=[],
        help="Fail if this source path has no mapped test owner; repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the map and print a pass/fail diagnostic.",
    )
    namespace = parser.parse_args(argv)
    return CliArgs(
        repo_root=namespace.repo_root,
        source=tuple(namespace.source),
        require_owned=tuple(namespace.require_owned),
        json_output=bool(namespace.json),
        check=bool(namespace.check),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ownership mapper and return a process exit code."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    records = build_ownership_map(repo_root)
    selected_records, unknown_sources = _select_records(records, args.source, repo_root)
    if unknown_sources:
        for source in unknown_sources:
            print(f"unknown source path: {source}", file=sys.stderr)
        return 2

    unowned_required = _required_unowned(records, args.require_owned, repo_root)
    if unowned_required:
        for source in unowned_required:
            print(f"unowned required source: {source}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(records_to_json(selected_records), indent=2, sort_keys=True))
    else:
        rendered = render_human(selected_records)
        if rendered:
            print(rendered)

    if args.check:
        owned = sum(1 for record in records if record.test_owners)
        print(f"test ownership map passed: {owned}/{len(records)} source file(s) have mapped tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
