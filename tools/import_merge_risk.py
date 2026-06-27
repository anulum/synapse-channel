#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — import graph merge-risk radar
"""Predict merge-risk between changed files and active claim scopes.

The radar stays local and dependency-free. It combines branch diffs or explicit
changed paths with claimed paths, Python import neighbours, CODEOWNERS, and the
repository's test ownership map. It reports likely merge-risk records; it does
not modify hub state and does not certify that a merge is safe.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import test_ownership_map  # noqa: E402

PACKAGE_NAME = "synapse_channel"
DEFAULT_SOURCE_ROOT = Path("src") / PACKAGE_NAME
CODEOWNERS_PATHS = (
    Path(".github") / "CODEOWNERS",
    Path("CODEOWNERS"),
    Path("docs") / "CODEOWNERS",
)
CLAIM_PATH_KEYS = frozenset(
    {
        "path",
        "paths",
        "claim_paths",
        "claimed_paths",
        "changed_files",
        "files",
        "generated_artifacts",
    }
)
CLAIM_CONTAINER_KEYS = frozenset({"claims", "active_claims", "tasks", "records"})


@dataclass(frozen=True)
class SourceImportRecord:
    """One source module and the package-local modules it imports."""

    source: str
    module: str
    imports: tuple[str, ...]


@dataclass(frozen=True)
class CodeOwnerRule:
    """One parsed CODEOWNERS pattern and its owners."""

    pattern: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class MergeRiskRecord:
    """One likely merge-risk relation between a changed path and a claim path."""

    kind: str
    changed_path: str
    claimed_path: str
    reason: str
    related_paths: tuple[str, ...]
    owners: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for the import merge-risk radar."""

    repo_root: Path
    changed_paths: tuple[Path, ...]
    claimed_paths: tuple[Path, ...]
    claims_json_paths: tuple[Path, ...]
    base: str | None
    head: str
    json_output: bool
    check: bool


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    """Return values without duplicates while preserving first-seen order."""
    return tuple(dict.fromkeys(values))


def _repo_relative(path: Path, repo_root: Path) -> str:
    """Return ``path`` as a repository-relative POSIX path when possible."""
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _module_name(source_path: Path, source_root: Path) -> str:
    """Return the importable module name for ``source_path``."""
    relative = source_path.relative_to(source_root).with_suffix("")
    if relative.name == "__init__":
        parts = relative.parent.parts
    else:
        parts = relative.parts
    return ".".join((PACKAGE_NAME, *parts))


def _parse_python(path: Path) -> ast.Module:
    """Parse one Python file into an AST."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_candidates(path: Path) -> tuple[str, ...]:
    """Return package-local import candidates discovered in one source file."""
    tree = _parse_python(path)
    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE_NAME or alias.name.startswith(f"{PACKAGE_NAME}."):
                    candidates.add(alias.name)
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            if node.module == PACKAGE_NAME or node.module.startswith(f"{PACKAGE_NAME}."):
                candidates.add(node.module)
                for alias in node.names:
                    if alias.name != "*":
                        candidates.add(f"{node.module}.{alias.name}")
    return tuple(sorted(candidates))


def build_import_graph(repo_root: Path = REPO_ROOT) -> tuple[SourceImportRecord, ...]:
    """Build an AST-based import graph for package-local source modules."""
    source_root = repo_root / DEFAULT_SOURCE_ROOT
    if not source_root.exists():
        return ()

    modules_by_path: dict[Path, str] = {
        path: _module_name(path, source_root)
        for path in sorted(source_root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    known_modules = frozenset(modules_by_path.values())
    records: list[SourceImportRecord] = []
    for path, module in modules_by_path.items():
        imports = tuple(
            candidate
            for candidate in _import_candidates(path)
            if candidate in known_modules and candidate != module
        )
        records.append(
            SourceImportRecord(
                source=_repo_relative(path, repo_root),
                module=module,
                imports=imports,
            )
        )
    return tuple(records)


def load_codeowners(repo_root: Path = REPO_ROOT) -> tuple[CodeOwnerRule, ...]:
    """Load CODEOWNERS rules from the first conventional file that exists."""
    for relative_path in CODEOWNERS_PATHS:
        codeowners_path = repo_root / relative_path
        if codeowners_path.exists():
            return _parse_codeowners(codeowners_path)
    return ()


def _parse_codeowners(path: Path) -> tuple[CodeOwnerRule, ...]:
    """Parse a CODEOWNERS file using deterministic last-match semantics."""
    rules: list[CodeOwnerRule] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append(CodeOwnerRule(pattern=parts[0], owners=tuple(parts[1:])))
    return tuple(rules)


def _codeowner_pattern_matches(pattern: str, path: str) -> bool:
    """Return whether a simplified CODEOWNERS pattern matches ``path``."""
    anchored = pattern.startswith("/")
    normalised = pattern.lstrip("/")
    if normalised.endswith("/"):
        prefix = normalised.rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    if anchored or "/" in normalised:
        return fnmatch.fnmatchcase(path, normalised)
    return fnmatch.fnmatchcase(Path(path).name, normalised) or fnmatch.fnmatchcase(path, normalised)


def codeowners_for_path(rules: Sequence[CodeOwnerRule], path: str) -> tuple[str, ...]:
    """Return the owners from the last CODEOWNERS rule matching ``path``."""
    owners: tuple[str, ...] = ()
    for rule in rules:
        if _codeowner_pattern_matches(rule.pattern, path):
            owners = rule.owners
    return owners


def _changed_paths_from_git(repo_root: Path, base: str | None, head: str) -> tuple[Path, ...]:
    """Return paths changed between ``base`` and ``head`` using git."""
    if base is None:
        return ()
    diff_ref = f"{base}...{head}"
    git_executable = shutil.which("git") or "git"
    result = subprocess.run(
        [git_executable, "-C", str(repo_root), "diff", "--name-only", diff_ref],
        check=False,
        capture_output=True,
        text=True,
    )  # nosec B603
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git diff failed"
        raise ValueError(message)
    return tuple(Path(line) for line in result.stdout.splitlines() if line.strip())


def _paths_from_claim_payload(payload: object) -> tuple[str, ...]:
    """Extract claimed paths from a flexible JSON claim snapshot payload."""
    if isinstance(payload, str):
        return (payload,)
    if isinstance(payload, list):
        return tuple(path for item in payload for path in _paths_from_claim_payload(item))
    if isinstance(payload, dict):
        paths: list[str] = []
        for key, value in payload.items():
            if key in CLAIM_PATH_KEYS or key in CLAIM_CONTAINER_KEYS:
                paths.extend(_paths_from_claim_payload(value))
        return tuple(paths)
    return ()


def _claimed_paths_from_json(paths: Sequence[Path]) -> tuple[Path, ...]:
    """Load claimed paths from one or more JSON claim snapshot files."""
    claimed: list[Path] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            msg = f"cannot read claims JSON {path}: {exc}"
            raise ValueError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = f"invalid claims JSON {path}: {exc.msg}"
            raise ValueError(msg) from exc
        claimed.extend(Path(value) for value in _paths_from_claim_payload(payload))
    return tuple(claimed)


def _path_overlaps(left: str, right: str) -> bool:
    """Return whether two repository paths have an exact or prefix overlap."""
    left_prefix = left.rstrip("/")
    right_prefix = right.rstrip("/")
    return (
        left_prefix == right_prefix
        or left_prefix.startswith(f"{right_prefix}/")
        or right_prefix.startswith(f"{left_prefix}/")
    )


def _source_by_path(
    records: Sequence[SourceImportRecord],
) -> dict[str, SourceImportRecord]:
    """Index import graph records by source path."""
    return {record.source: record for record in records}


def _test_owners_by_source(
    repo_root: Path,
) -> dict[str, tuple[str, ...]]:
    """Return test-owner paths indexed by source path."""
    records = test_ownership_map.build_ownership_map(repo_root)
    return {record.source: tuple(owner.path for owner in record.test_owners) for record in records}


def _risk_owners(
    codeowners: Sequence[CodeOwnerRule],
    changed_path: str,
    claimed_path: str,
) -> tuple[str, ...]:
    """Return combined CODEOWNERS for a changed/claimed path pair."""
    return tuple(
        sorted(
            {
                *codeowners_for_path(codeowners, changed_path),
                *codeowners_for_path(codeowners, claimed_path),
            }
        )
    )


def _risk_record(
    kind: str,
    changed_path: str,
    claimed_path: str,
    reason: str,
    related_paths: Iterable[str],
    owners: Iterable[str],
    tests: Iterable[str],
) -> MergeRiskRecord:
    """Build a normalised merge-risk record."""
    return MergeRiskRecord(
        kind=kind,
        changed_path=changed_path,
        claimed_path=claimed_path,
        reason=reason,
        related_paths=tuple(sorted(set(related_paths))),
        owners=tuple(sorted(set(owners))),
        tests=tuple(sorted(set(tests))),
    )


def find_merge_risks(
    repo_root: Path = REPO_ROOT,
    changed_paths: Sequence[Path] = (),
    claimed_paths: Sequence[Path] = (),
    claims_json_paths: Sequence[Path] = (),
    base: str | None = None,
    head: str = "HEAD",
) -> tuple[MergeRiskRecord, ...]:
    """Return likely merge-risk records for changed and claimed path scopes."""
    git_changed_paths = _changed_paths_from_git(repo_root, base, head)
    changed = _unique_ordered(
        _repo_relative(path, repo_root) for path in (*changed_paths, *git_changed_paths)
    )
    claimed_from_json = _claimed_paths_from_json(claims_json_paths)
    claimed = _unique_ordered(
        _repo_relative(path, repo_root) for path in (*claimed_paths, *claimed_from_json)
    )
    if not changed or not claimed:
        return ()

    import_graph = build_import_graph(repo_root)
    source_index = _source_by_path(import_graph)
    test_index = _test_owners_by_source(repo_root)
    codeowners = load_codeowners(repo_root)
    risks: dict[tuple[str, str, str, str], MergeRiskRecord] = {}

    for changed_path in changed:
        for claimed_path in claimed:
            owners = _risk_owners(codeowners, changed_path, claimed_path)
            if _path_overlaps(changed_path, claimed_path):
                record = _risk_record(
                    "direct-overlap",
                    changed_path,
                    claimed_path,
                    "changed path overlaps claimed path",
                    (),
                    owners,
                    (),
                )
                risks[(record.kind, record.changed_path, record.claimed_path, record.reason)] = (
                    record
                )

            changed_source = source_index.get(changed_path)
            claimed_source = source_index.get(claimed_path)
            if changed_source is not None and claimed_source is not None:
                import_reason = _import_neighbour_reason(changed_source, claimed_source)
                if import_reason is not None:
                    record = _risk_record(
                        "import-neighbour",
                        changed_path,
                        claimed_path,
                        import_reason,
                        (changed_path, claimed_path),
                        owners,
                        (),
                    )
                    risks[
                        (record.kind, record.changed_path, record.claimed_path, record.reason)
                    ] = record

                shared_tests = set(test_index.get(changed_path, ())).intersection(
                    test_index.get(claimed_path, ())
                )
                if shared_tests:
                    record = _risk_record(
                        "shared-test-owner",
                        changed_path,
                        claimed_path,
                        "changed and claimed sources share mapped tests",
                        shared_tests,
                        owners,
                        shared_tests,
                    )
                    risks[
                        (record.kind, record.changed_path, record.claimed_path, record.reason)
                    ] = record

    return tuple(
        risks[key]
        for key in sorted(
            risks,
            key=lambda item: (item[1], item[2], item[0], item[3]),
        )
    )


def _import_neighbour_reason(
    changed_source: SourceImportRecord,
    claimed_source: SourceImportRecord,
) -> str | None:
    """Return an import-neighbour reason for two source records, if any."""
    if claimed_source.module in changed_source.imports:
        return f"{changed_source.module} imports {claimed_source.module}"
    if changed_source.module in claimed_source.imports:
        return f"{claimed_source.module} imports {changed_source.module}"
    return None


def records_to_json(records: Sequence[MergeRiskRecord]) -> list[dict[str, object]]:
    """Convert merge-risk records into a stable JSON payload."""
    return [
        {
            "kind": record.kind,
            "changed_path": record.changed_path,
            "claimed_path": record.claimed_path,
            "reason": record.reason,
            "related_paths": list(record.related_paths),
            "owners": list(record.owners),
            "tests": list(record.tests),
        }
        for record in records
    ]


def render_human(records: Sequence[MergeRiskRecord]) -> str:
    """Render merge-risk records for terminal users."""
    if not records:
        return "import merge-risk radar: no risks found"

    lines = [f"import merge-risk radar: {len(records)} risk(s)"]
    for record in records:
        extras: list[str] = []
        if record.owners:
            extras.append(f"owners {', '.join(record.owners)}")
        if record.tests:
            extras.append(f"tests {', '.join(record.tests)}")
        if record.related_paths and not record.tests:
            extras.append(f"related {', '.join(record.related_paths)}")
        suffix = f" ({'; '.join(extras)})" if extras else ""
        lines.append(
            f"- {record.kind}: {record.changed_path} <-> "
            f"{record.claimed_path}: {record.reason}{suffix}"
        )
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> CliArgs:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Predict merge-risk from changed files, claims, imports, owners, and tests."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--changed",
        dest="changed_paths",
        type=Path,
        action="append",
        default=[],
        help="Changed path to compare with claims (repeatable).",
    )
    parser.add_argument(
        "--claimed",
        dest="claimed_paths",
        type=Path,
        action="append",
        default=[],
        help="Claimed path from an active claim snapshot (repeatable).",
    )
    parser.add_argument(
        "--claims-json",
        dest="claims_json_paths",
        type=Path,
        action="append",
        default=[],
        help="JSON file containing claimed paths from an external snapshot.",
    )
    parser.add_argument("--base", help="Base git ref for git diff --name-only BASE...HEAD.")
    parser.add_argument("--head", default="HEAD", help="Head git ref for branch diff input.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when risk records are found; exit 0 when none are found.",
    )
    args = parser.parse_args(argv)
    return CliArgs(
        repo_root=args.repo_root,
        changed_paths=tuple(args.changed_paths),
        claimed_paths=tuple(args.claimed_paths),
        claims_json_paths=tuple(args.claims_json_paths),
        base=args.base,
        head=args.head,
        json_output=args.json,
        check=args.check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the import merge-risk radar CLI."""
    args = _parse_args(argv)
    try:
        records = find_merge_risks(
            repo_root=args.repo_root,
            changed_paths=args.changed_paths,
            claimed_paths=args.claimed_paths,
            claims_json_paths=args.claims_json_paths,
            base=args.base,
            head=args.head,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(records_to_json(records), indent=2, sort_keys=True))
    else:
        print(render_human(records))
        if args.check and not records:
            print("import merge-risk radar passed: no risks found")

    if args.check and records:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
