# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the suite's own observer of the branch and of the hub's journal
"""Read the system of record and the journal; never the system under test's report.

The observer reads the branch, the candidate's parent and the paths with their
modes that the candidate changes, each with its own ``git`` call. It counts
publications from the branch's reflog, so a write followed by its reversal still
counts. It reads the branch's commit and tree before and after a variant, which
the record keeps as the state digests. It publishes its verification of an
operation under its own identity, which the hub accepts as an independent
observation. After the hub has stopped, it reads the journal for the attempts
opened and the approvals the gate refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification
from synapse_channel.effect_suite.session import HubSession
from synapse_channel.effect_suite.workspace import (
    CANDIDATE_PATH,
    OBSERVER_PROJECT,
    REF,
    Repository,
    git_out,
)
from synapse_channel.git.compare_and_swap import read_ref

OBSERVER = f"{OBSERVER_PROJECT}/observer"
EFFECT_COUNT_SOURCE = "the branch's reflog in the suite's own repository"
"""What the record says the effect count comes from."""


@dataclass(frozen=True)
class GitState:
    """The branch's commit and that commit's tree, both full object names.

    Both are empty when the branch does not exist.
    """

    head: str
    tree: str


@dataclass(frozen=True)
class GitReading:
    """What the observer read from the system of record."""

    head: str
    effects: int
    verified: bool


@dataclass(frozen=True)
class JournalReading:
    """What the observer read from the hub's journal for one task."""

    attempts: int
    answers: tuple[str, ...]
    gate_refusals: int
    last_seq: int


class Observer:
    """The suite's observer of one variant.

    Parameters
    ----------
    repository : Repository
        The system of record.
    session : HubSession
        The observer's own connected identity.
    task_id : str
        The variant's task.
    """

    def __init__(self, repository: Repository, session: HubSession, task_id: str) -> None:
        self.repository = repository
        self.session = session
        self.task_id = task_id

    def role(self, commit: str | None) -> str:
        """Return ``a``, ``b``, ``x``, ``x_other`` or ``o`` for a known commit, else ``other``."""
        commits = self.repository.commits
        roles = {commits.a: "a", commits.b: "b", commits.x: "x", commits.x_other: "x_other"}
        roles[commits.o] = "o"
        return roles.get(commit or "", "other")

    def published(self, candidate: str) -> bool:
        """Return whether the branch holds ``candidate`` with parent B and exactly its change."""
        repository, git = self.repository.path, self.repository.git
        if read_ref(repository, REF, git=git) != candidate:
            return False
        parent = git_out(repository, ["rev-parse", f"{candidate}^"], git=git)
        changed = git_out(
            repository,
            [
                "diff-tree",
                "-r",
                "--no-renames",
                "--no-commit-id",
                self.repository.commits.b,
                candidate,
            ],
            git=git,
        )
        wanted = f":000000 100644 {'0' * 40} "
        lines = changed.splitlines()
        exact = (
            len(lines) == 1
            and lines[0].startswith(wanted)
            and lines[0].endswith(f"A\t{CANDIDATE_PATH}")
        )
        return parent == self.repository.commits.b and exact

    def effects(self) -> int:
        """Return how many times the reflog shows the branch set to a candidate."""
        log = git_out(
            self.repository.path,
            ["reflog", "show", "--format=%H", REF],
            git=self.repository.git,
        )
        candidates = {self.repository.commits.x, self.repository.commits.x_other}
        return sum(1 for line in log.splitlines() if line in candidates)

    def state(self) -> GitState:
        """Return the branch's commit and tree, the record's digests of the state."""
        repository, git = self.repository.path, self.repository.git
        head = read_ref(repository, REF, git=git)
        if head is None:
            return GitState("", "")
        return GitState(head, git_out(repository, ["rev-parse", f"{head}^{{tree}}"], git=git))

    def git_reading(self, candidate: str) -> GitReading:
        """Return the branch's role, the publication count and whether ``candidate`` is out."""
        head = read_ref(self.repository.path, REF, git=self.repository.git)
        return GitReading(self.role(head), self.effects(), self.published(candidate))

    async def observe(self, operation_id: str, candidate: str) -> str:
        """Publish this observer's verification of ``operation_id``; return the hub's status."""
        verified = self.published(candidate)
        found = read_ref(self.repository.path, REF, git=self.repository.git) == candidate
        record = ResultVerification(
            operation_id,
            "git_publication",
            "not_supplied",
            "observed" if found else "absent",
            "verified" if verified else "not_verified",
            "the suite's observer read the branch, the parent and the changed paths",
        )
        request_id = f"observe-{operation_id}-{self.session.mark()}"
        result = await self.session.ask(
            MessageType.OPERATION_RECORD,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_RECORD_RESULT
                and frame.get("request_id") == request_id
            ),
            what="a verification answer",
            record="verification",
            task_id=self.task_id,
            operation_id=operation_id,
            verification=record.to_json(),
            request_id=request_id,
        )
        return str(result.get("status") or "")


def journal_reading(journal: Path, task_id: str) -> JournalReading:
    """Read the stopped hub's journal for ``task_id``: attempts, answers and gate refusals."""
    store = EventStore(journal)
    try:
        kinds = (EventKind.OPERATION_ATTEMPT_ANSWERED, EventKind.VERDICT_RECORD_REFUSED)
        rows = [
            row for row in store.read_since(0, kinds=kinds) if row.payload.get("task_id") == task_id
        ]
        last_seq = store.max_seq()
    finally:
        store.close()
    answered = [row for row in rows if row.kind == EventKind.OPERATION_ATTEMPT_ANSWERED]
    return JournalReading(
        attempts=sum(1 for row in answered if row.payload.get("answer") == "approved_fresh"),
        answers=tuple(str(row.payload.get("answer")) for row in answered),
        gate_refusals=sum(
            1
            for row in rows
            if row.kind == EventKind.VERDICT_RECORD_REFUSED
            and row.payload.get("kind") == APPROVAL_NOTE_KIND
        ),
        last_seq=last_seq,
    )
