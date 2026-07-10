# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — infer conservative function claims from local Git diffs
"""Map zero-context Git diff ranges to tree-sitter declaration scopes.

Only an ordinary modification can be narrowed. Additions, deletions, renames,
type changes, unsupported languages, oversized sources, syntax-error trees, and
any changed line outside a named declaration widen to the whole file. Modified
hunks are mapped on both sides, so deleting or renaming a function reserves its
old and new symbols rather than silently freeing one side.

Parser imports are lazy and come from the optional ``semantic`` extra. It uses
upstream pre-built grammar wheels for Python, JavaScript/JSX, TypeScript/TSX,
Rust, and Go; this module never downloads a parser or contacts a service.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git.semantic_scope import semantic_scope_path
from synapse_channel.git.semantic_tree_sitter import (
    Declaration,
    LanguageSpec,
    ParserFactory,
    default_parser,
    extract_declarations,
    language_for_path,
)

MAX_SEMANTIC_SOURCE_BYTES = 2 * 1024 * 1024
"""Largest source side parsed for narrowing; larger files stay whole-file."""

_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass(frozen=True)
class LineRange:
    """One one-based changed-line interval from a zero-context diff hunk."""

    start: int
    count: int


@dataclass(frozen=True)
class ChangedFile:
    """One tracked file delta and its old/new changed ranges."""

    status: str
    old_path: str
    new_path: str
    old_ranges: tuple[LineRange, ...] = ()
    new_ranges: tuple[LineRange, ...] = ()


@dataclass(frozen=True)
class SemanticDiffRecord:
    """Conservative claim projection for one changed tracked file."""

    status: str
    source: str
    old_source: str
    language: str | None
    symbols: tuple[str, ...]
    semantic_scopes: tuple[str, ...]
    claim_paths: tuple[str, ...]
    narrowed: bool
    reason: str


def _symbols_for_ranges(
    declarations: Sequence[Declaration], ranges: Sequence[LineRange]
) -> tuple[str, ...] | None:
    """Return smallest enclosing symbols, or ``None`` when any line is outside."""
    selected: list[str] = []
    for changed in ranges:
        for line in range(changed.start, changed.start + changed.count):
            candidates = tuple(
                declaration
                for declaration in declarations
                if declaration.start_line <= line <= declaration.end_line
            )
            if not candidates:
                return None
            smallest = min(
                candidates,
                key=lambda declaration: (
                    declaration.end_line - declaration.start_line,
                    -declaration.symbol.count("."),
                ),
            )
            selected.append(smallest.symbol)
    return tuple(dict.fromkeys(selected))


def _git(repo_root: Path, args: Sequence[str]) -> bytes:
    """Run a bounded local Git read and return stdout."""
    git = shutil.which("git")
    if git is None:
        raise ValueError("git semantic diff failed: git is not installed or not on PATH")
    result = subprocess.run(  # nosec B603
        [git, "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git semantic diff failed: {detail or 'unknown git error'}")
    return result.stdout


def _diff_args(base: str, head: str | None, paths: Sequence[str]) -> list[str]:
    """Return the shared revision/path arguments for Git diff commands."""
    args = [base]
    if head is not None:
        args.append(head)
    return [*args, "--", *paths]


def _changed_files(
    repo_root: Path,
    *,
    base: str,
    head: str | None,
    paths: Sequence[str],
) -> tuple[ChangedFile, ...]:
    """Read tracked file statuses and zero-context hunk ranges from Git."""
    raw = _git(
        repo_root,
        ["diff", "--name-status", "-z", "--find-renames", *_diff_args(base, head, paths)],
    )
    fields = raw.rstrip(b"\0").split(b"\0") if raw else []
    changed: list[ChangedFile] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="replace")
        index += 1
        code = status[:1]
        old_path = os.fsdecode(fields[index])
        index += 1
        new_path = old_path
        if code in {"R", "C"}:
            new_path = os.fsdecode(fields[index])
            index += 1
        old_ranges: tuple[LineRange, ...] = ()
        new_ranges: tuple[LineRange, ...] = ()
        if code == "M":
            patch = _git(
                repo_root,
                [
                    "diff",
                    "--no-ext-diff",
                    "--no-color",
                    "--unified=0",
                    *_diff_args(base, head, (new_path,)),
                ],
            )
            hunks = _HUNK.findall(patch)
            old_ranges = tuple(
                LineRange(int(old), int(old_count or b"1"))
                for old, old_count, _new, _new_count in hunks
                if int(old_count or b"1")
            )
            new_ranges = tuple(
                LineRange(int(new), int(new_count or b"1"))
                for _old, _old_count, new, new_count in hunks
                if int(new_count or b"1")
            )
        changed.append(ChangedFile(code, old_path, new_path, old_ranges, new_ranges))
    return tuple(changed)


def _revision_source(repo_root: Path, revision: str, path: str) -> bytes:
    """Read one source from a Git revision."""
    return _git(repo_root, ["show", f"{revision}:{path}"])


def _working_source(repo_root: Path, path: str) -> bytes:
    """Read one non-symlink regular file from the working tree."""
    source_path = repo_root / path
    if source_path.is_symlink() or not source_path.is_file():
        raise OSError(f"working-tree source is not a regular file: {path}")
    return source_path.read_bytes()


def _whole_file(changed: ChangedFile, language: str | None, reason: str) -> SemanticDiffRecord:
    """Return a conservative whole-file record."""
    source = changed.old_path if changed.status == "D" else changed.new_path
    return SemanticDiffRecord(
        status=changed.status,
        source=source,
        old_source=changed.old_path,
        language=language,
        symbols=(),
        semantic_scopes=(),
        claim_paths=(source,),
        narrowed=False,
        reason=reason,
    )


def _narrow_modified(
    repo_root: Path,
    changed: ChangedFile,
    *,
    base: str,
    head: str | None,
    language: str,
    spec: LanguageSpec,
    parser_factory: ParserFactory,
) -> SemanticDiffRecord:
    """Narrow one ordinary modification or widen it on incomplete evidence."""
    try:
        old_source = _revision_source(repo_root, base, changed.old_path)
        new_source = (
            _revision_source(repo_root, head, changed.new_path)
            if head is not None
            else _working_source(repo_root, changed.new_path)
        )
    except OSError:
        return _whole_file(changed, language, "source side is not a regular file")
    if max(len(old_source), len(new_source)) > MAX_SEMANTIC_SOURCE_BYTES:
        return _whole_file(changed, language, "source exceeds semantic parser size ceiling")
    try:
        old_declarations = extract_declarations(old_source, spec, parser_factory=parser_factory)
        new_declarations = extract_declarations(new_source, spec, parser_factory=parser_factory)
    except UnicodeDecodeError:
        return _whole_file(changed, language, "declaration name is not valid UTF-8")
    old_symbols = _symbols_for_ranges(old_declarations, changed.old_ranges)
    new_symbols = _symbols_for_ranges(new_declarations, changed.new_ranges)
    if old_symbols is None or new_symbols is None or (not old_symbols and not new_symbols):
        return _whole_file(changed, language, "changed line is outside a named declaration")
    symbols = tuple(dict.fromkeys((*old_symbols, *new_symbols)))
    try:
        scopes = tuple(semantic_scope_path(changed.new_path, symbol) for symbol in symbols)
    except ValueError:
        return _whole_file(changed, language, "declaration cannot form a safe semantic path")
    return SemanticDiffRecord(
        status=changed.status,
        source=changed.new_path,
        old_source=changed.old_path,
        language=language,
        symbols=symbols,
        semantic_scopes=scopes,
        claim_paths=scopes,
        narrowed=True,
        reason="all changed lines map to named declarations",
    )


def resolve_git_diff(
    repo_root: Path,
    *,
    base: str,
    head: str | None = None,
    paths: Sequence[str] = (),
    parser_factory: ParserFactory = default_parser,
) -> tuple[SemanticDiffRecord, ...]:
    """Resolve a tracked Git diff into semantic or whole-file claim records."""
    if not base.strip() or (head is not None and not head.strip()):
        raise ValueError("semantic diff revisions must not be blank")
    records: list[SemanticDiffRecord] = []
    for changed in _changed_files(repo_root, base=base, head=head, paths=paths):
        language_entry = language_for_path(changed.new_path)
        if changed.status != "M":
            language = language_entry[0] if language_entry is not None else None
            reason = f"git status {changed.status} is file-wide"
            records.append(_whole_file(changed, language, reason))
        elif language_entry is None:
            records.append(_whole_file(changed, None, "language is not supported for narrowing"))
        elif not changed.old_ranges and not changed.new_ranges:
            records.append(_whole_file(changed, language_entry[0], "diff has no textual hunks"))
        else:
            language, spec = language_entry
            records.append(
                _narrow_modified(
                    repo_root,
                    changed,
                    base=base,
                    head=head,
                    language=language,
                    spec=spec,
                    parser_factory=parser_factory,
                )
            )
    return tuple(records)


def records_to_json(records: Sequence[SemanticDiffRecord]) -> list[dict[str, object]]:
    """Return stable JSON-compatible semantic diff evidence."""
    return [
        {
            "kind": "diff",
            "status": record.status,
            "source": record.source,
            "old_source": record.old_source,
            "language": record.language,
            "symbols": list(record.symbols),
            "semantic_scopes": list(record.semantic_scopes),
            "claim_paths": list(record.claim_paths),
            "narrowed": record.narrowed,
            "reason": record.reason,
        }
        for record in records
    ]
