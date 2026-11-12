# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the effect with its marker, and the seal, as Git changes read back
"""Make an attempt's Git changes and read back what they did.

The **effect** moves ``refs/heads/<branch>`` from exactly the declared base to
the candidate and creates the attempt's marker holding the ``applied`` blob, in
one atomic change. The **seal** creates the same marker holding the ``sealed``
blob, which no later effect can pass. Each has a local form, a ref transaction
(``git update-ref --stdin``) in the repository itself, and a remote form, an
atomic push whose leases say the branch must hold the base and the marker must
not exist yet.

After every change the branch and the marker are read back, and
:func:`synapse_channel.core.attempt_markers.change_result` names the result:
the exit code never decides on its own. Every call runs through the bounded
runner of :mod:`synapse_channel.git.git_runtime`. Nothing here writes to a
hosted service unless a caller names one as the remote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.attempt_markers import (
    APPLIED,
    OBJECT_ID_LENGTHS,
    SEALED,
    AttemptMarker,
    MarkerObjects,
    change_result,
    observation,
)
from synapse_channel.git.git_runtime import GitError, run_git

_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")
"""Branch names this module accepts; ``git check-ref-format --branch`` then has the last word."""


@dataclass(frozen=True)
class ChangeOutcome:
    """What one effect or seal did, as read back from the system of record.

    Attributes
    ----------
    answer : str
        One of :data:`synapse_channel.core.attempt_markers.CHANGE_RESULTS`.
    succeeded : bool
        Whether the Git command exited 0.
    marker, branch : str or None
        The marker's object and the branch's commit read back; ``None`` when absent.
    detail : str
        Git's own message when the command did not succeed (at most 200 characters).
    """

    answer: str
    succeeded: bool
    marker: str | None
    branch: str | None
    detail: str


@dataclass(frozen=True)
class Target:
    """Where a change goes: a repository holding the objects, a branch and its declared base.

    ``remote`` names the repository or URL to push to; ``None`` changes the
    repository itself.
    """

    repository: Path
    branch: str
    base: str
    remote: str | None = None

    @property
    def branch_ref(self) -> str:
        """The branch's full ref name."""
        return "refs/heads/" + self.branch


def _git_out(target: Target, args: list[str], *, git: str, input_text: str | None = None) -> str:
    completed = run_git(git, target.repository, args, input_text=input_text)
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:200] or f"exit code {completed.returncode}"
        raise GitError(f"git {args[0]} failed: {detail}")
    return completed.stdout.strip()


def object_format(target: Target, *, git: str) -> str:
    """Return the repository's object format, ``sha1`` or ``sha256``.

    Raises
    ------
    GitError
        When Git cannot tell, or names a format this module does not know.
    """
    found = _git_out(target, ["rev-parse", "--show-object-format"], git=git)
    if found not in OBJECT_ID_LENGTHS:
        raise GitError(f"the repository's object format {found!r} is not sha1 or sha256")
    return found


def _check(target: Target, candidate: str | None, *, git: str) -> None:
    """Refuse a branch name or commit names that cannot be what the change says."""
    if _BRANCH.fullmatch(target.branch) is None:
        raise GitError(f"branch {target.branch!r} is not a plain branch name")
    _git_out(target, ["check-ref-format", "--branch", target.branch], git=git)
    fmt = object_format(target, git=git)
    pattern = re.compile(f"[0-9a-f]{{{OBJECT_ID_LENGTHS[fmt]}}}")
    named = (("base", target.base), ("candidate", candidate))
    for name, value in named:
        if value is not None and pattern.fullmatch(value) is None:
            raise GitError(f"{name} {value!r} is not a full {fmt} commit name")


def write_marker_objects(target: Target, marker: AttemptMarker, *, git: str) -> MarkerObjects:
    """Write both marker blobs into the repository and return their ids.

    Raises
    ------
    GitError
        When Git stores a blob under an id other than the one computed without it.
    """
    objects = marker.objects(object_format(target, git=git))
    for kind, expected in ((APPLIED, objects.applied), (SEALED, objects.sealed)):
        text = marker.blob(kind).decode("ascii")
        stored = _git_out(target, ["hash-object", "-w", "--stdin"], git=git, input_text=text)
        if stored != expected:
            raise GitError(f"git stored the {kind} marker as {stored}, expected {expected}")
    return objects


def _read_local(target: Target, ref: str, *, git: str) -> str | None:
    completed = run_git(git, target.repository, ["rev-parse", "--verify", "--quiet", ref])
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _read_back(target: Target, marker_ref: str, *, git: str) -> tuple[str | None, str | None]:
    """Return the marker's object and the branch's commit, from the remote when there is one."""
    if target.remote is None:
        return _read_local(target, marker_ref, git=git), _read_local(
            target, target.branch_ref, git=git
        )
    listed = _git_out(target, ["ls-remote", target.remote, target.branch_ref, marker_ref], git=git)
    found: dict[str, str] = {}
    for line in listed.splitlines():
        object_id, _tab, ref = line.partition("\t")
        found[ref] = object_id
    return found.get(marker_ref), found.get(target.branch_ref)


def _change(
    target: Target,
    marker: AttemptMarker,
    objects: MarkerObjects,
    *,
    kind: str,
    candidate: str | None,
    git: str,
) -> ChangeOutcome:
    """Run one change in its local or remote form, read it back and name the result."""
    blob = objects.applied if kind == APPLIED else objects.sealed
    if target.remote is None:
        lines = ["start"]
        if candidate is not None:
            lines.append(f"update {target.branch_ref} {candidate} {target.base}")
        lines += [f"create {marker.ref} {blob}", "prepare", "commit"]
        args = ["update-ref", "--stdin"]
        completed = run_git(git, target.repository, args, input_text="\n".join(lines) + "\n")
    else:
        leases = [f"--force-with-lease={marker.ref}:"]
        specs = [f"{blob}:{marker.ref}"]
        if candidate is not None:
            leases.insert(0, f"--force-with-lease={target.branch_ref}:{target.base}")
            specs.insert(0, f"{candidate}:{target.branch_ref}")
        args = ["push", "--atomic", "--porcelain", *leases, target.remote, *specs]
        completed = run_git(git, target.repository, args)
    marker_read, branch_read = _read_back(target, marker.ref, git=git)
    succeeded = completed.returncode == 0
    answer = change_result(
        succeeded=succeeded,
        marker=marker_read,
        branch=branch_read,
        objects=objects,
        kind=kind,
        base=target.base,
        branch_set_to=candidate,
    )
    detail = "" if succeeded else (completed.stderr.strip() or completed.stdout.strip())[:200]
    return ChangeOutcome(answer, succeeded, marker_read, branch_read, detail)


def apply_effect(
    target: Target, marker: AttemptMarker, *, candidate: str, git: str
) -> ChangeOutcome:
    """Move the branch from exactly the base to ``candidate`` and create the ``applied`` marker.

    Both happen in one change or neither does. The result is read back.

    Raises
    ------
    GitError
        When the branch or a commit name is malformed, a Git call misses its
        deadline, or a read-back cannot be made.
    """
    _check(target, candidate, git=git)
    objects = write_marker_objects(target, marker, git=git)
    return _change(target, marker, objects, kind=APPLIED, candidate=candidate, git=git)


def seal(target: Target, marker: AttemptMarker, *, git: str) -> ChangeOutcome:
    """Create the attempt's ``sealed`` marker, so that its effect can never land.

    The branch is not touched; it is read back for the record.

    Raises
    ------
    GitError
        As :func:`apply_effect`.
    """
    _check(target, None, git=git)
    objects = write_marker_objects(target, marker, git=git)
    return _change(target, marker, objects, kind=SEALED, candidate=None, git=git)


def observe(target: Target, marker: AttemptMarker, *, candidate: str, git: str) -> str:
    """Return what the branch and the marker of this attempt say, in the repository itself.

    Raises
    ------
    GitError
        When ``target`` names a remote: the ancestry check needs the commits here.
    """
    if target.remote is not None:
        raise GitError("an observation reads the repository itself, not a remote")
    objects = marker.objects(object_format(target, git=git))
    marker_read, branch_read = _read_back(target, marker.ref, git=git)
    contains: bool | None = branch_read == candidate
    if branch_read is not None and branch_read != candidate:
        args = ["merge-base", "--is-ancestor", candidate, branch_read]
        code = run_git(git, target.repository, args).returncode
        contains = {0: True, 1: False}.get(code)
    return observation(
        branch=branch_read,
        marker=marker_read,
        objects=objects,
        base=target.base,
        candidate=candidate,
        contains_candidate=contains,
    )
