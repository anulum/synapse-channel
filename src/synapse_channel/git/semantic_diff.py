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
import signal
import subprocess  # nosec B404
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from synapse_channel.core.errors import SynapseError
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

MAX_GIT_STDOUT_BYTES = 8 * 1024 * 1024
"""Largest non-source Git evidence stream retained in memory."""

MAX_GIT_STDERR_BYTES = 64 * 1024
"""Largest Git diagnostic stream retained before the command is stopped."""

GIT_READ_TIMEOUT_SECONDS = 10.0
"""Hard deadline for one local semantic-evidence Git command."""

_GIT_TERMINATE_GRACE_SECONDS = 0.5
_GIT_PIPE_CHUNK_BYTES = 64 * 1024
_MAX_GIT_ERROR_CHARS = 512

_REGULAR_GIT_MODES = frozenset({b"100644", b"100755"})
"""Canonical non-executable and executable regular-file modes stored by Git."""

_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
"""Canonical SHA-1 empty tree used by Git for an unborn branch comparison."""

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
    evidence_error: str | None = None


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


class _SemanticGitReadError(SynapseError, ValueError):
    """Git could not provide bounded, trustworthy semantic evidence."""

    code = "semantic_git_read"


def _git_environment(git: str) -> dict[str, str]:
    """Return the minimal deterministic environment for local Git reads."""
    env = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_EXTERNAL_DIFF": "",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PAGER": "cat",
        "PATH": str(Path(git).parent),
        "TERM": "dumb",
    }
    for key in ("COMSPEC", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _terminal_safe_detail(raw: bytes) -> str:
    """Return one bounded printable line without terminal control characters."""
    decoded = raw.decode("utf-8", errors="replace")
    printable = "".join(character if character.isprintable() else " " for character in decoded)
    detail = " ".join(printable.split())
    if len(detail) > _MAX_GIT_ERROR_CHARS:
        detail = f"{detail[:_MAX_GIT_ERROR_CHARS]}..."
    return detail


def _drain_git_pipe(
    stream: BinaryIO,
    output: bytearray,
    *,
    limit: int,
    label: str,
    overflow: threading.Event,
    overflow_labels: list[str],
    reader_errors: list[str],
    overflow_lock: threading.Lock,
) -> None:
    """Drain one child pipe while retaining at most ``limit`` bytes."""
    try:
        while chunk := stream.read(_GIT_PIPE_CHUNK_BYTES):
            room = limit - len(output)
            if len(chunk) > room:
                if room > 0:
                    output.extend(chunk[:room])
                with overflow_lock:
                    if not overflow_labels:
                        overflow_labels.append(label)
                overflow.set()
                return
            output.extend(chunk)
    except Exception:
        with overflow_lock:
            if label not in reader_errors:
                reader_errors.append(label)
        overflow.set()


def _terminate_git_process(
    process: subprocess.Popen[bytes],
    *,
    posix: bool = os.name != "nt",
) -> None:
    """Stop the isolated Git process, escalating once after a short grace."""
    if process.poll() is not None:
        return
    try:
        if posix:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=_GIT_TERMINATE_GRACE_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is not None:
            return
    try:
        if posix:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=_GIT_TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if process.poll() is not None:
            return
        raise _SemanticGitReadError(
            "git semantic diff failed: git command could not be terminated"
        ) from exc


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


def _git(
    repo_root: Path,
    args: Sequence[str],
    *,
    max_stdout_bytes: int = MAX_GIT_STDOUT_BYTES,
    allow_stdout_truncation: bool = False,
) -> bytes:
    """Run one local Git read with bounded streams, deadline, and teardown."""
    git = shutil.which("git")
    if git is None:
        raise _SemanticGitReadError("git semantic diff failed: git is not installed or not on PATH")
    if max_stdout_bytes < 0:
        raise ValueError("git semantic diff stdout limit must not be negative")
    try:
        process = subprocess.Popen(  # nosec B603
            [
                git,
                "-c",
                "core.fsmonitor=false",
                "--no-pager",
                "-C",
                str(repo_root),
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(git),
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            ),
        )
    except OSError as exc:
        raise _SemanticGitReadError("git semantic diff failed: could not start git") from exc
    if process.stdout is None or process.stderr is None:
        _terminate_git_process(process)
        raise _SemanticGitReadError("git semantic diff failed: git pipes were unavailable")
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    overflow_labels: list[str] = []
    reader_errors: list[str] = []
    overflow_lock = threading.Lock()
    readers = (
        threading.Thread(
            target=_drain_git_pipe,
            args=(process.stdout, stdout),
            kwargs={
                "limit": max_stdout_bytes,
                "label": "stdout",
                "overflow": overflow,
                "overflow_labels": overflow_labels,
                "reader_errors": reader_errors,
                "overflow_lock": overflow_lock,
            },
            daemon=True,
        ),
        threading.Thread(
            target=_drain_git_pipe,
            args=(process.stderr, stderr),
            kwargs={
                "limit": MAX_GIT_STDERR_BYTES,
                "label": "stderr",
                "overflow": overflow,
                "overflow_labels": overflow_labels,
                "reader_errors": reader_errors,
                "overflow_lock": overflow_lock,
            },
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + GIT_READ_TIMEOUT_SECONDS
    timed_out = False
    while process.poll() is None and not overflow.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        overflow.wait(min(0.01, remaining))
    if timed_out or overflow.is_set():
        _terminate_git_process(process)
    else:
        process.wait(timeout=_GIT_TERMINATE_GRACE_SECONDS)
    for reader in readers:
        reader.join(timeout=_GIT_TERMINATE_GRACE_SECONDS)
    if any(reader.is_alive() for reader in readers):
        _terminate_git_process(process)
        raise _SemanticGitReadError("git semantic diff failed: git output streams did not close")
    if reader_errors:
        raise _SemanticGitReadError(
            f"git semantic diff failed: git {reader_errors[0]} stream read failed"
        )
    if timed_out:
        raise _SemanticGitReadError("git semantic diff failed: git command timed out")
    if overflow_labels:
        if overflow_labels[0] == "stdout" and allow_stdout_truncation:
            return bytes(stdout)
        raise _SemanticGitReadError(
            f"git semantic diff failed: git {overflow_labels[0]} exceeded its byte limit"
        )
    if process.returncode:
        detail = _terminal_safe_detail(bytes(stderr))
        raise _SemanticGitReadError(f"git semantic diff failed: {detail or 'unknown git error'}")
    return bytes(stdout)


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
    cached: bool,
) -> tuple[ChangedFile, ...]:
    """Read tracked file statuses and zero-context hunk ranges from Git."""
    revision_args = _diff_args(base, head, paths)
    if cached:
        revision_args.insert(0, "--cached")
    raw = _git(
        repo_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "--find-renames",
            *revision_args,
        ],
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
            patch_args = _diff_args(base, head, (new_path,))
            if cached:
                patch_args.insert(0, "--cached")
            try:
                patch = _git(
                    repo_root,
                    [
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-color",
                        "--unified=0",
                        *patch_args,
                    ],
                )
            except _SemanticGitReadError as exc:
                changed.append(
                    ChangedFile(
                        code,
                        old_path,
                        new_path,
                        evidence_error=str(exc),
                    )
                )
                continue
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
    """Read one regular-file source from a Git revision."""
    listing = _git(repo_root, ["ls-tree", "-z", revision, "--", path])
    _require_regular_git_entry(listing, path=path, location=f"revision {revision}")
    return _git(
        repo_root,
        ["cat-file", "blob", f"{revision}:{path}"],
        max_stdout_bytes=MAX_SEMANTIC_SOURCE_BYTES + 1,
        allow_stdout_truncation=True,
    )


def _working_source(repo_root: Path, path: str) -> bytes:
    """Read one non-symlink regular file through the parser size ceiling."""
    source_path = repo_root / path
    if source_path.is_symlink() or not source_path.is_file():
        raise OSError(f"working-tree source is not a regular file: {path}")
    with source_path.open("rb") as stream:
        return stream.read(MAX_SEMANTIC_SOURCE_BYTES + 1)


def _index_source(repo_root: Path, path: str) -> bytes:
    """Read one regular-file blob from the Git index."""
    listing = _git(repo_root, ["ls-files", "--stage", "-z", "--", path])
    _require_regular_git_entry(listing, path=path, location="index")
    return _git(
        repo_root,
        ["cat-file", "blob", f":{path}"],
        max_stdout_bytes=MAX_SEMANTIC_SOURCE_BYTES + 1,
        allow_stdout_truncation=True,
    )


def _require_regular_git_entry(raw: bytes, *, path: str, location: str) -> None:
    """Reject missing, ambiguous, symlink, and gitlink entries before parsing."""
    entries = tuple(entry for entry in raw.rstrip(b"\0").split(b"\0") if entry)
    if len(entries) != 1:
        raise OSError(f"{location} source is not one regular file: {path}")
    mode = entries[0].split(b" ", 1)[0]
    if mode not in _REGULAR_GIT_MODES:
        raise OSError(f"{location} source is not a regular file: {path}")


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
    cached: bool,
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
            else (
                _index_source(repo_root, changed.new_path)
                if cached
                else _working_source(repo_root, changed.new_path)
            )
        )
    except OSError:
        return _whole_file(changed, language, "source side is not a regular file")
    except _SemanticGitReadError:
        return _whole_file(changed, language, "safe Git source evidence is unavailable")
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
    cached: bool = False,
    parser_factory: ParserFactory = default_parser,
) -> tuple[SemanticDiffRecord, ...]:
    """Resolve a tracked Git diff into semantic or whole-file claim records.

    Parameters
    ----------
    repo_root : pathlib.Path
        Git worktree whose local objects and files are inspected.
    base : str
        Base revision for the old source side.
    head : str or None, optional
        Committed new source side. Omit for the working tree or staged index.
    paths : Sequence[str], optional
        Repository-relative path filter.
    cached : bool, optional
        Compare ``base`` with the staged index rather than the working tree.
        Cannot be combined with ``head``.
    parser_factory : ParserFactory, optional
        Configured local tree-sitter parser factory.

    Returns
    -------
    tuple[SemanticDiffRecord, ...]
        One conservative projection per changed tracked file.

    Raises
    ------
    ValueError
        If revisions are blank, ``cached`` is combined with ``head``, or Git
        cannot supply the requested diff.
    """
    if not base.strip() or (head is not None and not head.strip()):
        raise ValueError("semantic diff revisions must not be blank")
    if cached and head is not None:
        raise ValueError("staged semantic diffs cannot also specify a head revision")
    records: list[SemanticDiffRecord] = []
    for changed in _changed_files(
        repo_root,
        base=base,
        head=head,
        paths=paths,
        cached=cached,
    ):
        language_entry = language_for_path(changed.new_path)
        if changed.status != "M":
            language = language_entry[0] if language_entry is not None else None
            reason = f"git status {changed.status} is file-wide"
            records.append(_whole_file(changed, language, reason))
        elif language_entry is None:
            records.append(_whole_file(changed, None, "language is not supported for narrowing"))
        elif changed.evidence_error is not None:
            records.append(
                _whole_file(changed, language_entry[0], "safe Git diff evidence is unavailable")
            )
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
                    cached=cached,
                    language=language,
                    spec=spec,
                    parser_factory=parser_factory,
                )
            )
    return tuple(records)


def resolve_staged_diff(
    repo_root: Path,
    *,
    paths: Sequence[str] = (),
    parser_factory: ParserFactory = default_parser,
) -> tuple[SemanticDiffRecord, ...]:
    """Resolve the staged index for commit-time semantic enforcement.

    Parameters
    ----------
    repo_root : pathlib.Path
        Git worktree whose authoritative index is inspected.
    paths : Sequence[str], optional
        Repository-relative physical source filter.
    parser_factory : ParserFactory, optional
        Configured local tree-sitter parser factory.

    Returns
    -------
    tuple[SemanticDiffRecord, ...]
        Conservative semantic or whole-file evidence for staged changes.

    Raises
    ------
    ValueError
        If Git state is unreadable or a missing ``HEAD`` is not a verified
        unborn branch. A verified initial commit compares against Git's empty
        tree so additions remain whole-file.
    """
    base = _staged_base(repo_root)
    return resolve_git_diff(
        repo_root,
        base=base,
        paths=paths,
        cached=True,
        parser_factory=parser_factory,
    )


def _staged_base(repo_root: Path) -> str:
    """Return ``HEAD`` or the empty tree for a verified unborn branch."""
    try:
        _git(repo_root, ["rev-parse", "--verify", "HEAD"])
    except ValueError as head_error:
        status = _git(repo_root, ["status", "--porcelain=v2", "--branch"])
        if b"# branch.oid (initial)" not in status.splitlines():
            raise head_error
        return _EMPTY_TREE
    return "HEAD"


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
