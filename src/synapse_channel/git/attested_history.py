# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — M3 collector: attestation classification of main history
"""Classify main-history advances as attested or unattested (the M3 collector).

:mod:`synapse_channel.core.governance_metrics` defines the M1–M4 governance
metrics as a pure computation over observed events; this module is the first
real collector behind that contract. It walks a repository's first-parent main
history, gathers attestation evidence from a directory of audit artifacts
(session logs, handovers, audit records — any text that cites commit hashes),
and classifies every main advance: a move is **attested** when some artifact
cites a hexadecimal token of at least :data:`MIN_ATTESTATION_HEX` characters
that prefixes the move's full hash, and **unattested** otherwise.

Prefix citation is accepted because git short hashes are generated unique
repo-wide, and a seven-character collision against a specific history is of the
order of ``16**-7`` — negligible, though an all-letter English word such as
"defaced" scans as a hex token and could in principle collide the same way.
Evidence gathering is deliberately conservative in the other direction too:
only files under the artifact directory are read, so a hash cited nowhere reads
as unattested even if a human could vouch for it. This measures the M3 rate on
the evidence trail we actually keep, which is the point of the wedge.

The git subprocess is injectable (``runner``) so classification is
unit-testable without a real repository, matching
:mod:`synapse_channel.git.gitclaim`.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.governance_metrics import (
    GovernanceMetrics,
    MainMoveEvent,
    compute_governance_metrics,
)
from synapse_channel.git.gitclaim import GitError, GitRunner

MIN_ATTESTATION_HEX = 7
"""Shortest hexadecimal citation accepted as attestation evidence.

Seven is git's default short-hash width; anything shorter is too collision
prone to count as citing a specific object.
"""

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
"""Largest artifact file read for evidence; larger files are skipped.

Audit artifacts are prose and logs. Anything larger is a bundle or a raw
dump whose scan cost dwarfs its evidentiary value — a real coordination tree
holds tens-of-megabytes run dumps that would turn one measurement into
minutes of I/O.
"""

_HEX_TOKEN = re.compile(rf"\b[0-9a-f]{{{MIN_ATTESTATION_HEX},40}}\b")
_FULL_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class MainMoveRecord:
    """One first-parent main advance and its attestation classification."""

    sha: str
    subject: str
    attested: bool


def _default_git_runner(args: list[str]) -> str:
    """Run ``git <args>`` and return stripped stdout, raising :class:`GitError`.

    Parameters
    ----------
    args : list[str]
        The argv after ``git``, built internally by this module.

    Returns
    -------
    str
        The command's standard output with surrounding whitespace removed.

    Raises
    ------
    GitError
        When git is not installed or the command exits non-zero.
    """
    git = shutil.which("git")
    if git is None:
        raise GitError("git is not installed or not on PATH")
    try:
        # Fixed git binary, no shell, bounded argv from internal git operations.
        result = subprocess.run(  # nosec B603
            [git, *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"git {' '.join(args)} exited non-zero"
        raise GitError(detail) from exc
    return result.stdout.strip()


def list_main_moves(
    repo: Path,
    *,
    ref: str = "origin/main",
    limit: int | None = None,
    runner: GitRunner = _default_git_runner,
) -> tuple[tuple[str, str], ...]:
    """Return ``(sha, subject)`` for each first-parent advance, oldest first.

    Parameters
    ----------
    repo : Path
        The repository whose history is walked.
    ref : str
        The ref whose first-parent history constitutes "main moves".
    limit : int | None
        Walk only the newest ``limit`` moves; ``None`` walks the whole history.
    runner : GitRunner
        The git subprocess, injectable for testing.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Full hash and subject per move, oldest to newest.

    Raises
    ------
    GitError
        When git fails — for example an unknown ref or a missing repository.
    """
    args = ["-C", str(repo), "log", "--first-parent", "--reverse"]
    if limit is not None:
        args.append(f"--max-count={limit}")
    args.extend(["--pretty=format:%H%x09%s", ref])
    output = runner(args)
    moves: list[tuple[str, str]] = []
    for line in output.splitlines():
        sha, _, subject = line.partition("\t")
        if _FULL_SHA.match(sha):
            moves.append((sha, subject))
    return tuple(moves)


def collect_attestation_tokens(
    artifact_dir: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES
) -> frozenset[str]:
    """Return every hexadecimal citation found under an artifact directory.

    Every regular file under ``artifact_dir`` no larger than ``max_bytes`` is
    read as UTF-8 text with undecodable bytes ignored, and every word-bounded
    lowercase hexadecimal token of :data:`MIN_ATTESTATION_HEX` to 40
    characters is collected. The scan is case-insensitive on input (citations
    are lowered first) and never raises on unreadable files — evidence that
    cannot be read is evidence we do not have.

    Parameters
    ----------
    artifact_dir : Path
        The directory tree holding audit artifacts; may be empty or absent.
    max_bytes : int, optional
        Files larger than this are skipped as dumps rather than artifacts.

    Returns
    -------
    frozenset[str]
        The distinct hexadecimal tokens cited anywhere in the artifacts.
    """
    tokens: set[str] = set()
    if not artifact_dir.is_dir():
        return frozenset()
    for path in sorted(artifact_dir.rglob("*")):
        try:
            if not path.is_file() or path.stat().st_size > max_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tokens.update(_HEX_TOKEN.findall(text.lower()))
    return frozenset(tokens)


def classify_main_moves(
    moves: tuple[tuple[str, str], ...],
    tokens: frozenset[str],
) -> tuple[MainMoveRecord, ...]:
    """Classify each main move against the cited attestation tokens.

    Parameters
    ----------
    moves : tuple[tuple[str, str], ...]
        ``(sha, subject)`` pairs from :func:`list_main_moves`.
    tokens : frozenset[str]
        Hexadecimal citations from :func:`collect_attestation_tokens`.

    Returns
    -------
    tuple[MainMoveRecord, ...]
        One record per move, in the input order; a move is attested when any
        token prefixes its full hash.
    """
    return tuple(
        MainMoveRecord(
            sha=sha,
            subject=subject,
            attested=any(sha.startswith(token) for token in tokens),
        )
        for sha, subject in moves
    )


def main_history_metrics(
    repo: Path,
    artifact_dir: Path,
    *,
    ref: str = "origin/main",
    limit: int | None = None,
    runner: GitRunner = _default_git_runner,
) -> tuple[GovernanceMetrics, tuple[MainMoveRecord, ...]]:
    """Measure M3 on a real repository against a real evidence trail.

    Parameters
    ----------
    repo : Path
        The repository whose first-parent ``ref`` history is measured.
    artifact_dir : Path
        The audit-artifact tree providing attestation citations.
    ref : str
        The ref whose advances count as main moves.
    limit : int | None
        Measure only the newest ``limit`` moves; ``None`` measures them all.
    runner : GitRunner
        The git subprocess, injectable for testing.

    Returns
    -------
    tuple[GovernanceMetrics, tuple[MainMoveRecord, ...]]
        The computed metrics (only the M3 family is populated — this collector
        observes no edit, push, or claim-violation events) and the per-move
        records behind them.

    Raises
    ------
    GitError
        When git fails — for example an unknown ref or a missing repository.
    """
    moves = list_main_moves(repo, ref=ref, limit=limit, runner=runner)
    records = classify_main_moves(moves, collect_attestation_tokens(artifact_dir))
    events = [MainMoveEvent(had_exact_object_artifact=record.attested) for record in records]
    return compute_governance_metrics(events), records


__all__ = [
    "MIN_ATTESTATION_HEX",
    "MainMoveRecord",
    "classify_main_moves",
    "collect_attestation_tokens",
    "list_main_moves",
    "main_history_metrics",
]
