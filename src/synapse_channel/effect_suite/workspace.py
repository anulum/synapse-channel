# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the disposable system of record and hub of one suite variant
"""Prepare a throwaway Git repository and serve a throwaway hub for one variant.

The repository is the system of record. Its branch ``main`` holds the base B,
whose parent is A. Three prepared commits exist but no ref points at them: the
candidate X and the other candidate X′, both children of B, and O, a sibling
used to move the base under the executor. Every ref update is logged
(``core.logAllRefUpdates=always``), so the observer can count publications
from the reflog.

The hub keeps its journal in the same folder. It stops serving, releases what
it opened itself, and closes the journal when the variant ends.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.git.compare_and_swap import run_git
from synapse_channel.git.git_runtime import GitError

BRANCH = "main"
REF = "refs/heads/main"
CANDIDATE_PATH = "report.txt"
"""The one file the candidate commits add; its mode is ``100644``."""

PROJECT = "SUITE"
OBSERVER_PROJECT = "OBSERVE"


@dataclass(frozen=True)
class Commits:
    """The commits of the prepared history, by role."""

    a: str
    b: str
    x: str
    x_other: str
    o: str


@dataclass(frozen=True)
class Repository:
    """The system of record of one variant."""

    path: Path
    git: str
    commits: Commits


@dataclass(frozen=True)
class SuiteHub:
    """A serving hub and where its journal lives."""

    uri: str
    journal: Path


def git_out(repository: Path, args: list[str], *, git: str) -> str:
    """Run one ``git`` command in ``repository`` and return its stripped output.

    Raises
    ------
    GitError
        When the command fails or misses its deadline.
    """
    completed = run_git(git, repository, args)
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:200] or f"exit code {completed.returncode}"
        raise GitError(f"git {args[0]} failed: {detail}")
    return completed.stdout.strip()


def _commit(repository: Path, git: str, parent: str | None, name: str, text: str) -> str:
    """Return a commit of ``parent``'s tree plus ``name`` with ``text``, moving no ref."""
    git_out(repository, ["read-tree", parent] if parent else ["read-tree", "--empty"], git=git)
    (repository / name).write_text(text, encoding="utf-8")
    git_out(repository, ["add", name], git=git)
    tree = git_out(repository, ["write-tree"], git=git)
    (repository / name).unlink()
    args = ["commit-tree", tree, "-m", f"suite: {name}"]
    if parent is not None:
        args += ["-p", parent]
    return git_out(repository, args, git=git)


def prepare_repository(directory: Path, *, git: str) -> Repository:
    """Create the system of record in ``directory`` with ``main`` at B."""
    path = directory / "system-of-record"
    path.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "effect suite"],
        ["config", "user.email", "effect-suite@example.invalid"],
        ["config", "core.logAllRefUpdates", "always"],
    ):
        git_out(path, args, git=git)
    a = _commit(path, git, None, "seed.txt", "seed\n")
    b = _commit(path, git, a, "base.txt", "base\n")
    git_out(path, ["update-ref", REF, b], git=git)
    x = _commit(path, git, b, CANDIDATE_PATH, "published by the suite\n")
    x_other = _commit(path, git, b, CANDIDATE_PATH, "another candidate\n")
    o = _commit(path, git, b, "moved.txt", "moved by someone else\n")
    return Repository(path, git, Commits(a, b, x, x_other, o))


def suite_policy() -> AclPolicy:
    """Return claims and messages for all; operation evidence for the suite and the observer.

    The executor's project holds ``operation-evidence`` so that the executor can
    end its own attempt with its outcome; its own verifications count for nothing.
    """
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "anyone may claim"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
            AclRule(OPERATION_EVIDENCE, "claim", "suite-*", PROJECT, "the executor ends"),
            AclRule(OPERATION_EVIDENCE, "claim", "suite-*", OBSERVER_PROJECT, "the observer"),
        ]
    )


@contextlib.asynccontextmanager
async def suite_hub(directory: Path, *, verdict_gate: bool = True) -> AsyncIterator[SuiteHub]:
    """Serve a hub on a kernel-assigned port with its journal in ``directory``.

    On the way out the hub stops serving, releases what it opened itself, and the
    journal is closed, in that order.
    """
    journal = directory / "hub.db"
    store = EventStore(journal)
    hub = SynapseHub(journal=store, acl_policy=suite_policy(), verdict_gate_enabled=verdict_gate)
    serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        _host, port = await hub.wait_until_serving(timeout=30.0)
        yield SuiteHub(f"ws://127.0.0.1:{port}", journal)
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        hub.close()
        store.close()
