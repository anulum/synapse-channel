# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — structure budget over a pushed range: every commit against its own parent
"""Judge every commit of a pushed range against its own first parent.

A fast-forward push can carry several commits. Comparing only the pushed tip
with its parent misses a ceiling that an earlier commit of the range raised
and the tip kept: the tip's ledger then equals its parent's, and nothing seems
to rise. This walk judges each commit ``C`` of
``git rev-list --reverse --first-parent BEFORE..TO`` the way the pre-commit
hook judged it: ``C``'s ledger against ``C``'s own sources, read from Git
objects, and against the ledger of ``C``'s first parent.

The range is anchored strictly:

* ``BEFORE`` must be an ancestor of ``TO``; a rewritten history is refused.
* An all-zero ``BEFORE`` (a newly created ref) walks from the merge base with
  ``--default-branch``; when that merge base is ``TO`` itself, ``TO`` alone is
  judged against its parent, and when there is none the walk is refused.
* A range that ``git rev-list`` cannot list is refused, never judged as its tip
  alone.
* An empty range (``BEFORE`` equal to ``TO``) judges ``TO`` against its parent.
* More than ``--max-commits`` commits (100 by default) is refused, naming the
  command to run by hand.

Each commit falls in exactly one of four cases:

* **skipped, by name:** neither the commit nor its first parent holds a ledger;
  the commit predates the budget;
* **adoption:** the parent holds no ledger and the commit does; the commit's
  sources are judged against its own ledger;
* **refused, by name:** the parent holds a ledger and the commit removes it, or
  holds one that cannot be parsed (an emptied ledger has no schema), so a
  delete-then-raise pair is refused at the delete;
* **otherwise:** the full check against the commit's sources and the parent's
  ledger.

Nothing in the working tree or the index is read or written.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from tools.check_structure_budget import (
    LedgerError,
    Violation,
    check_baseline,
    check_tree,
    load_baseline,
    parse_ledger,
)
from tools.structure_revision import measure_revision

__all__ = ["DEFAULT_MAX_COMMITS", "check_commit", "run_walk", "walk_range"]

DEFAULT_MAX_COMMITS = 100
"""The largest pushed range judged in CI; a longer one is refused and names the manual command.

One fast-forward push of local main is never split to fit a bound, so the bound is set
above any push the group makes in practice.
"""


def _git(root: Path, *arguments: str) -> tuple[int, str]:
    """Run one read-only ``git`` command and return its status and output."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise LedgerError(f"baseline_unavailable: cannot run git: {error}") from error
    return done.returncode, done.stdout.strip()


def _commit(root: Path, revision: str) -> str:
    """Resolve ``revision`` to a commit id, or refuse it by name."""
    status, out = _git(root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if status != 0:
        raise LedgerError(f"walk_refused: unknown revision {revision!r}")
    return out


def _is_zero(revision: str) -> bool:
    """Return whether ``revision`` is Git's all-zero id for a ref that did not exist."""
    return bool(revision) and set(revision) == {"0"}


def walk_range(
    root: Path, before: str, to: str, *, default_branch: str | None, max_commits: int
) -> tuple[str, ...]:
    """Return the commits to judge, oldest first, or refuse the range by name.

    Parameters
    ----------
    root : Path
        Repository root with the range's history fetched.
    before : str
        The ref's previous tip, or all zeros for a newly created ref.
    to : str
        The pushed tip.
    default_branch : str or None
        The ref an all-zero ``before`` is anchored to by merge base.
    max_commits : int
        The largest range accepted.

    Returns
    -------
    tuple[str, ...]
        Commit ids in first-parent order, oldest first; a side branch merged
        into the range is judged only through its merge commit. When
        ``before`` equals ``to`` the range is empty, and ``to`` alone is
        returned, so the tip is still judged against its parent.

    Raises
    ------
    LedgerError
        When a revision is unknown, ``before`` is not an ancestor of ``to``, an
        all-zero ``before`` has no default branch or no merge base with it, the
        range cannot be listed, or the range is too long.
    """
    tip = _commit(root, to)
    if _is_zero(before):
        if not default_branch:
            raise LedgerError("walk_refused: an all-zero --walk-from needs --default-branch")
        status, base = _git(root, "merge-base", tip, _commit(root, default_branch))
        if status != 0:
            raise LedgerError(
                f"walk_refused: git merge-base {to} {default_branch} failed with status {status}; "
                "the new ref shares no history with the default branch"
            )
        if base == tip:
            return (tip,)
        start = base
    else:
        start = _commit(root, before)
        status, _ = _git(root, "merge-base", "--is-ancestor", start, tip)
        if status != 0:
            raise LedgerError(f"walk_refused: {before} is not an ancestor of {to}")
    status, listing = _git(root, "rev-list", "--reverse", "--first-parent", f"{start}..{tip}")
    if status != 0:
        # An empty listing would judge the tip alone, which is the gap this walk closes.
        raise LedgerError(f"walk_refused: git rev-list {start}..{tip} failed with status {status}")
    commits = tuple(line for line in listing.splitlines() if line)
    if len(commits) > max_commits:
        raise LedgerError(
            f"walk_refused: {len(commits)} commits exceed the bound of {max_commits}; run "
            f"`python -m tools.check_structure_budget --check --walk-from {before} --to {to} "
            f"--max-commits {len(commits)}` by hand"
        )
    return commits or (tip,)


def check_commit(
    root: Path, commit: str, ledger_path: str, today: datetime.date
) -> tuple[list[Violation], bool]:
    """Judge one commit against its own sources and its first parent's ledger.

    Returns
    -------
    tuple[list[Violation], bool]
        The violations, and whether the commit predates the budget and was
        skipped (neither it nor its parent holds a ledger).

    Raises
    ------
    LedgerError
        When the commit removes the ledger its parent holds, or a ledger is malformed.
    """
    parent_status, parent = _git(root, "rev-parse", "--verify", "--quiet", f"{commit}^1")
    baseline = load_baseline(root, parent, ledger_path) if parent_status == 0 else None
    status, text = _git(root, "show", f"{commit}:{ledger_path}")
    if status != 0:
        if baseline is not None:
            raise LedgerError(f"ledger_removed: {commit} drops the {ledger_path} its parent holds")
        return [], True
    ledger = parse_ledger(text + "\n")
    try:
        figures = measure_revision(
            root, commit, ledger.roots, allowed_private=ledger.allowed_private
        )
    except ValueError as error:
        raise LedgerError(f"baseline_unavailable: {error}") from error
    found = check_tree(ledger, figures, today)
    if baseline is not None:
        found += check_baseline(ledger, baseline)
    return found, False


def run_walk(
    root: Path,
    *,
    before: str,
    to: str,
    ledger_path: str,
    default_branch: str | None,
    max_commits: int,
) -> int:
    """Judge a pushed range and print one line per finding; return the exit status.

    Returns
    -------
    int
        ``0`` when every commit holds, ``1`` on any violation, and ``2`` when the
        range or a commit cannot be judged.
    """
    try:
        commits = walk_range(
            root, before, to, default_branch=default_branch, max_commits=max_commits
        )
        total = 0
        for commit in commits:
            found, skipped = check_commit(root, commit, ledger_path, datetime.date.today())
            if skipped:
                print(f"{commit}: predates the structure budget; skipped")
            for violation in found:
                print(f"{commit}: {violation.render()}")
            total += len(found)
    except (LedgerError, SyntaxError, UnicodeDecodeError) as error:
        print(f"structure budget cannot be judged: {error}")
        return 2
    if total:
        print(f"structure budget: {total} violation(s) across {len(commits)} commit(s)")
        return 1
    print(f"structure budget holds on every commit: {len(commits)} judged")
    return 0
