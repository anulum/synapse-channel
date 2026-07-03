# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared blackboard: the task plan and the progress stream
"""Shared blackboard for the Synapse hub: a task ledger and a progress ledger.

The blackboard is the team's shared plan, kept separate from the lease registry
in :mod:`synapse_channel.core.state`. A :class:`LedgerTask` *declares* a unit of work
— its title, description, and dependencies — so any agent can read the board and
pick something ready to do; a :class:`~synapse_channel.core.state.TaskClaim` is the
*lease* on actually doing it. The two share a ``task_id`` namespace but stay
independent: a task can sit on the board with no claim, and an ad-hoc claim can
exist with no board entry, so the simple claim flow keeps working untouched.

* **Task ledger** (:attr:`Blackboard.tasks`) — the plan: declared tasks with a
  coarse planning ``status`` (``open``/``in_progress``/``blocked``/``done``/
  ``cancelled``) and a dependency edge set. Dependency cycles are refused so the
  plan stays a DAG and :meth:`Blackboard.ready_tasks` is well-defined.
* **Progress ledger** (:attr:`Blackboard.progress`) — an append-only, bounded
  stream of structured progress notes (``note``/``blocked``/``assessment``) that
  a supervisor can read to spot stalls, distinct from human-facing chat.

The blackboard is transport-agnostic and synchronous: the hub owns one instance
and mutates it from its event loop, with injectable ``now`` for deterministic
tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LEDGER_TASK_STATUSES = frozenset({"open", "in_progress", "blocked", "done", "cancelled"})
"""Allowed planning statuses for a :class:`LedgerTask`."""

DEFAULT_LEDGER_TASK_STATUS = "open"
"""Status a freshly declared task starts in."""

PROGRESS_KINDS = frozenset({"note", "blocked", "assessment", "usage", "approval"})
"""Allowed kinds for a :class:`ProgressNote`.

``usage`` marks an opt-in model cost/token record whose text body follows the
canonical accounting format (see :mod:`synapse_channel.core.accounting`).
``approval`` marks a human-in-the-loop approval request or decision whose text
body follows the canonical approval format (see
:mod:`synapse_channel.core.approvals`).
"""

DEFAULT_MAX_PROGRESS = 5000
"""Default cap on retained progress notes before the oldest are dropped."""

DEFAULT_MAX_PROGRESS_PER_AUTHOR = 1000
"""Default cap on retained progress notes per author."""

DEFAULT_MAX_PROGRESS_PER_TASK = 1000
"""Default cap on retained progress notes per task."""

TERMINAL_LEDGER_STATUSES = frozenset({"done", "cancelled"})
"""Planning statuses that satisfy a dependency edge (the task is finished)."""


@dataclass
class LedgerTask:
    """A declared unit of work on the shared plan.

    Attributes
    ----------
    task_id : str
        Stable identifier, shared with any claim taken on the task.
    title : str
        Short human-readable name of the work.
    description : str
        Optional longer description or acceptance notes.
    depends_on : tuple[str, ...]
        Task ids that must reach a terminal status before this task is ready.
    status : str
        Coarse planning status from :data:`LEDGER_TASK_STATUSES`.
    suggested_owner : str
        Optional agent name proposed to take the task; advisory only.
    created_by : str
        Agent that first declared the task.
    created_at : float
        Wall-clock seconds when the task was first declared.
    updated_at : float
        Wall-clock seconds when the task was last changed.
    """

    task_id: str
    title: str
    created_at: float
    updated_at: float
    description: str = ""
    depends_on: tuple[str, ...] = ()
    status: str = DEFAULT_LEDGER_TASK_STATUS
    suggested_owner: str = ""
    created_by: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this task."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "suggested_owner": self.suggested_owner,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ProgressNote:
    """One structured entry in the append-only progress ledger.

    Attributes
    ----------
    task_id : str
        Task the note concerns; ``""`` for a board-wide note.
    author : str
        Agent that posted the note.
    kind : str
        One of :data:`PROGRESS_KINDS`.
    text : str
        Free-form body of the note.
    posted_at : float
        Wall-clock seconds when the note was posted.
    """

    task_id: str
    author: str
    kind: str
    text: str
    posted_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this note."""
        return {
            "task_id": self.task_id,
            "author": self.author,
            "kind": self.kind,
            "text": self.text,
            "posted_at": self.posted_at,
        }


def _clean_depends_on(task_id: str, depends_on: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Normalise a dependency list: strip, drop blanks, self, and duplicates."""
    seen: dict[str, None] = {}
    for raw in depends_on:
        dep = str(raw).strip()
        if dep and dep != task_id:
            seen.setdefault(dep, None)
    return tuple(seen)


class Blackboard:
    """The team's shared plan: a task ledger plus an append-only progress stream.

    The board is single-threaded and synchronous; the hub owns one instance and
    mutates it from its event loop. Posting a task is an upsert — the same id
    re-declares the task and replaces its planning fields — so a planner can
    refine the plan idempotently.

    Parameters
    ----------
    max_progress : int, optional
        Maximum progress notes retained; the oldest are dropped beyond this
        bound so the stream cannot grow without limit. Clamped up to ``1``.
        Defaults to :data:`DEFAULT_MAX_PROGRESS`.
    max_progress_per_author : int, optional
        Maximum progress notes retained for one author. Clamped up to ``1``.
        Defaults to :data:`DEFAULT_MAX_PROGRESS_PER_AUTHOR`.
    max_progress_per_task : int, optional
        Maximum progress notes retained for one task id. Clamped up to ``1``.
        Defaults to :data:`DEFAULT_MAX_PROGRESS_PER_TASK`.
    """

    def __init__(
        self,
        max_progress: int = DEFAULT_MAX_PROGRESS,
        *,
        max_progress_per_author: int = DEFAULT_MAX_PROGRESS_PER_AUTHOR,
        max_progress_per_task: int = DEFAULT_MAX_PROGRESS_PER_TASK,
    ) -> None:
        self.tasks: dict[str, LedgerTask] = {}
        self.progress: list[ProgressNote] = []
        self.max_progress = max(int(max_progress), 1)
        self.max_progress_per_author = max(int(max_progress_per_author), 1)
        self.max_progress_per_task = max(int(max_progress_per_task), 1)

    def post_task(
        self,
        *,
        task_id: str,
        title: str,
        author: str,
        description: str = "",
        depends_on: tuple[str, ...] | list[str] = (),
        suggested_owner: str = "",
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Declare or re-declare a task on the plan (an upsert).

        Parameters
        ----------
        task_id, title : str
            Identifier and short name; both are required (whitespace-stripped).
        author : str
            Agent declaring the task; recorded as ``created_by`` on first post.
        description : str, optional
            Longer description.
        depends_on : tuple[str, ...] or list[str], optional
            Prerequisite task ids; self-references and duplicates are dropped.
        suggested_owner : str, optional
            Advisory proposed owner.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the id or
            title is missing or the dependencies would form a cycle.
        """
        tid = task_id.strip()
        name = title.strip()
        if not tid:
            return False, "Task ID is required."
        if not name:
            return False, "Task title is required."

        deps = _clean_depends_on(tid, depends_on)
        if self._would_cycle(tid, deps):
            return False, f"Task '{tid}' dependencies would form a cycle."

        ts = time.time() if now is None else float(now)
        existing = self.tasks.get(tid)
        if existing is None:
            self.tasks[tid] = LedgerTask(
                task_id=tid,
                title=name,
                created_at=ts,
                updated_at=ts,
                description=description.strip(),
                depends_on=deps,
                suggested_owner=suggested_owner.strip(),
                created_by=author,
            )
            return True, f"Task '{tid}' declared by {author}."

        existing.title = name
        existing.description = description.strip()
        existing.depends_on = deps
        existing.suggested_owner = suggested_owner.strip()
        existing.updated_at = ts
        return True, f"Task '{tid}' re-declared by {author}."

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Change a declared task's planning status or suggested owner.

        Parameters
        ----------
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New planning status; must be in :data:`LEDGER_TASK_STATUSES`.
        suggested_owner : str or None, optional
            Replacement advisory owner (``""`` clears it).
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on success, ``(False, reason)`` when the task is
            unknown or the status is not a recognised planning status.
        """
        task = self.tasks.get(task_id.strip())
        if task is None:
            return False, f"Task '{task_id}' is not on the board."
        if status is not None and status not in LEDGER_TASK_STATUSES:
            return False, f"Unknown ledger status '{status}'."

        if status is not None:
            task.status = status
        if suggested_owner is not None:
            task.suggested_owner = suggested_owner.strip()
        task.updated_at = time.time() if now is None else float(now)
        return True, f"Task '{task_id}' plan updated."

    def post_progress(
        self,
        *,
        task_id: str,
        author: str,
        text: str,
        kind: str = "note",
        now: float | None = None,
    ) -> tuple[bool, ProgressNote | str]:
        """Append a structured progress note, dropping the oldest past the bound.

        Parameters
        ----------
        task_id : str
            Task the note concerns; ``""`` for a board-wide note.
        author : str
            Agent posting the note.
        text : str
            Body of the note.
        kind : str, optional
            One of :data:`PROGRESS_KINDS`. Defaults to ``"note"``.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        tuple[bool, ProgressNote or str]
            ``(True, note)`` on success, ``(False, reason)`` for an unknown kind.
        """
        if kind not in PROGRESS_KINDS:
            return False, f"Unknown progress kind '{kind}'."
        ts = time.time() if now is None else float(now)
        return True, self._append(
            ProgressNote(task_id=task_id.strip(), author=author, kind=kind, text=text, posted_at=ts)
        )

    def note(
        self, *, task_id: str, author: str, text: str, now: float | None = None
    ) -> ProgressNote:
        """Append a plain ``note``-kind progress entry, returning it directly.

        A convenience over :meth:`post_progress` for callers that always use the
        ``note`` kind (so the kind cannot be rejected) and want the appended
        :class:`ProgressNote` without unpacking a result tuple.

        Parameters
        ----------
        task_id : str
            Task the note concerns; ``""`` for a board-wide note.
        author : str
            Agent posting the note.
        text : str
            Body of the note.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        ProgressNote
            The appended note.
        """
        ts = time.time() if now is None else float(now)
        return self._append(
            ProgressNote(
                task_id=task_id.strip(), author=author, kind="note", text=text, posted_at=ts
            )
        )

    def _append(self, note: ProgressNote) -> ProgressNote:
        """Append a note, dropping the oldest beyond each bound, and return it."""
        self.progress.append(note)
        self._drop_oldest_matching(lambda _candidate: True, self.max_progress)
        self._drop_oldest_matching(
            lambda candidate: candidate.author == note.author,
            self.max_progress_per_author,
        )
        self._drop_oldest_matching(
            lambda candidate: candidate.task_id == note.task_id,
            self.max_progress_per_task,
        )
        return note

    def restore_progress(self, note: ProgressNote) -> ProgressNote:
        """Restore one persisted progress note while applying retention bounds.

        Parameters
        ----------
        note : ProgressNote
            Persisted note to insert into the retained progress stream.

        Returns
        -------
        ProgressNote
            The restored note.
        """
        return self._append(note)

    def _drop_oldest_matching(
        self, predicate: Callable[[ProgressNote], bool], max_count: int
    ) -> None:
        """Drop oldest retained notes matching ``predicate`` until under ``max_count``."""
        matching_indexes = [
            index for index, candidate in enumerate(self.progress) if predicate(candidate)
        ]
        excess = len(matching_indexes) - max_count
        if excess <= 0:
            return
        drop_indexes = set(matching_indexes[:excess])
        self.progress = [
            note for index, note in enumerate(self.progress) if index not in drop_indexes
        ]

    def blocking_dependencies(self, task_id: str) -> list[str]:
        """Return the unmet dependencies of a task, in declaration order.

        A dependency is unmet when the prerequisite is absent from the board or
        has not reached a terminal status. Returns an empty list for an unknown
        task.

        Parameters
        ----------
        task_id : str
            Identifier of the task to inspect.

        Returns
        -------
        list[str]
            Task ids that still block this task.
        """
        task = self.tasks.get(task_id.strip())
        if task is None:
            return []
        blocking: list[str] = []
        for dep in task.depends_on:
            other = self.tasks.get(dep)
            if other is None or other.status not in TERMINAL_LEDGER_STATUSES:
                blocking.append(dep)
        return blocking

    def ready_tasks(self) -> list[LedgerTask]:
        """Return open tasks whose every dependency has reached a terminal status.

        Returns
        -------
        list[LedgerTask]
            Tasks with planning status ``open`` and no blocking dependency,
            sorted by ``task_id``.
        """
        ready = [
            task
            for task in self.tasks.values()
            if task.status == "open" and not self.blocking_dependencies(task.task_id)
        ]
        return sorted(ready, key=lambda t: t.task_id)

    def _would_cycle(self, task_id: str, depends_on: tuple[str, ...]) -> bool:
        """Return whether giving ``task_id`` these dependencies closes a cycle.

        Walks the existing dependency graph from each proposed dependency; a
        cycle exists if any walk reaches ``task_id`` again. Dependencies on tasks
        not yet on the board cannot close a cycle and are ignored by the walk.
        """
        stack = list(depends_on)
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            other = self.tasks.get(current)
            if other is not None:
                stack.extend(other.depends_on)
        return False

    def snapshot(self, *, task_cap: int | None = None) -> dict[str, Any]:
        """Return a consistent view of the plan and the recent progress stream.

        Parameters
        ----------
        task_cap : int or None, optional
            When set, bound the served ``tasks`` list (floored at ``1``):
            every live task is kept ahead of any terminal one, the newest
            ``updated_at`` wins inside each class when trimming, and the
            reply carries ``total_tasks`` and ``truncated`` so a consumer
            sees the bound instead of mistaking the page for the whole
            plan. ``ready`` always lists every ready id — ids are cheap,
            the task bodies are what outgrow a frame. ``None`` serves the
            full board unchanged.

        Returns
        -------
        dict[str, Any]
            Mapping with ``tasks`` (sorted by id), ``ready`` (ready task
            ids), and ``progress`` (the retained notes in order); under a
            cap also ``total_tasks`` and ``truncated``.
        """
        ordered = sorted(self.tasks.values(), key=lambda t: t.task_id)
        ready = [task.task_id for task in self.ready_tasks()]
        notes = [note.as_dict() for note in self.progress]
        if task_cap is None:
            return {
                "tasks": [task.as_dict() for task in ordered],
                "ready": ready,
                "progress": notes,
            }
        cap = max(1, int(task_cap))
        live = [task for task in ordered if task.status not in TERMINAL_LEDGER_STATUSES]
        terminal = [task for task in ordered if task.status in TERMINAL_LEDGER_STATUSES]
        if len(live) >= cap:
            kept = sorted(live, key=lambda t: t.updated_at, reverse=True)[:cap]
        else:
            fill = sorted(terminal, key=lambda t: t.updated_at, reverse=True)[: cap - len(live)]
            kept = live + fill
        kept_ordered = sorted(kept, key=lambda t: t.task_id)
        return {
            "tasks": [task.as_dict() for task in kept_ordered],
            "ready": ready,
            "progress": notes,
            "total_tasks": len(ordered),
            "truncated": len(kept_ordered) < len(ordered),
        }
