# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in ready-task dispatcher client
"""Opt-in dispatcher: nudge project seats about ready board tasks, exactly once.

The worker polls the hub's board/state/manifest/who snapshots, computes a
deterministic plan with :mod:`synapse_channel.core.ready_dispatch`, and for
every assignment performs the smallest mutation that wakes a seat:

1. a compare-and-set ``ledger_task_update`` pinning ``suggested_owner`` (the
   task's snapshot ``version`` as the guard — a conflict aborts the nudge), and
2. a directed wake chat to the seat's online identity under a stable
   idempotency key.

Hard boundary, enforced by construction: the worker sends only
``ledger_task_update``, directed ``chat``, ``ledger_progress``, and one
singleton-lease ``claim``/renew on the synthetic ``dispatch:<project>`` task
id (the multi-dispatcher guard). It never claims real tasks, releases,
hands off, locks, approves, or broadcasts. The woken agent claims the task
and updates its status itself.

Exactly-once across crashes: an append-only outbox (JSONL) records every
assignment intent before the first send. On startup the worker reconciles
pending intents against live board state — claimed tasks are marked
delivered, vanished/re-owned tasks conflicted, and unanswered ones retried
with the SAME idempotency key (hub-side dedupe replays rather than
duplicates), bounded by ``max_attempts``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.ready_dispatch import DispatchAssignment, plan_dispatches

LEASE_TASK_PREFIX = "dispatch:"
"""Synthetic board task id claimed by the singleton dispatcher of a project."""

DEFAULT_MAX_ATTEMPTS = 3
"""Wake retries per assignment before the intent is abandoned (still logged)."""

_OUTBOX_STATES = frozenset({"pending", "delivered", "conflicted", "abandoned"})


@dataclass(frozen=True)
class OutboxIntent:
    """One recorded dispatch intent with its delivery state.

    Attributes
    ----------
    wake_id : str
        Unique intent id (task id + owner + task version).
    task_id : str
        Board task the intent nudges for.
    owner : str
        Assigned seat.
    wake_identity : str
        Online identity the wake targeted.
    task_version : int
        Board version the CAS pinned at assignment time.
    idem_key : str
        Stable idempotency key of the wake message.
    attempts : int
        Wake sends issued so far.
    state : str
        ``pending``, ``delivered``, ``conflicted``, or ``abandoned``.
    assigned_at : float
        Wall-clock seconds of the first intent record.
    """

    wake_id: str
    task_id: str
    owner: str
    wake_identity: str
    task_version: int
    idem_key: str
    attempts: int
    state: str
    assigned_at: float


class DispatchOutbox:
    """Append-only JSONL record of dispatch intents (crash-safe exactly-once).

    State transitions are appended as events; the current state of an intent
    is the replay of its event log, so a crash mid-send never loses which
    idempotency keys were already used.
    """

    def __init__(self, path: Path) -> None:
        """Load (or create) the outbox at ``path``."""
        self.path = path
        self._intents: dict[str, OutboxIntent] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._apply(json.loads(line))
                except ValueError:
                    continue

    def _apply(self, event: Mapping[str, Any]) -> None:
        wake_id = str(event.get("wake_id") or "")
        if not wake_id:
            return
        current = self._intents.get(wake_id)
        self._intents[wake_id] = OutboxIntent(
            wake_id=wake_id,
            task_id=str(event.get("task_id", current.task_id if current else "")),
            owner=str(event.get("owner", current.owner if current else "")),
            wake_identity=str(event.get("wake_identity", current.wake_identity if current else "")),
            task_version=int(event.get("task_version", current.task_version if current else 0)),
            idem_key=str(event.get("idem_key", current.idem_key if current else "")),
            attempts=int(event.get("attempts", current.attempts if current else 0)),
            state=str(event.get("state", current.state if current else "pending")),
            assigned_at=float(event.get("assigned_at", current.assigned_at if current else 0.0)),
        )

    def _append(self, intent: OutboxIntent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "wake_id": intent.wake_id,
                        "task_id": intent.task_id,
                        "owner": intent.owner,
                        "wake_identity": intent.wake_identity,
                        "task_version": intent.task_version,
                        "idem_key": intent.idem_key,
                        "attempts": intent.attempts,
                        "state": intent.state,
                        "assigned_at": intent.assigned_at,
                    },
                    separators=(",", ":"),
                )
                + "\n",
            )
        self._intents[intent.wake_id] = intent

    def record_assignment(
        self, assignment: DispatchAssignment, task_version: int, *, now: float
    ) -> OutboxIntent:
        """Record a new pending intent, reusing any existing one's idem key."""
        wake_id = f"{assignment.task_id}:{assignment.owner}:v{task_version}"
        existing = self._intents.get(wake_id)
        if existing is not None:
            return existing
        intent = OutboxIntent(
            wake_id=wake_id,
            task_id=assignment.task_id,
            owner=assignment.owner,
            wake_identity=assignment.wake_identity,
            task_version=task_version,
            idem_key=f"dispatch-wake-{assignment.task_id}-{assignment.owner}-v{task_version}",
            attempts=0,
            state="pending",
            assigned_at=now,
        )
        self._append(intent)
        return intent

    def transition(
        self, intent: OutboxIntent, *, state: str | None = None, attempts: int | None = None
    ) -> OutboxIntent:
        """Append a state transition for ``intent`` and return the new value."""
        new_state = intent.state if state is None else state
        if new_state not in _OUTBOX_STATES:
            raise ValueError(f"unknown outbox state {new_state!r}")
        updated = OutboxIntent(
            wake_id=intent.wake_id,
            task_id=intent.task_id,
            owner=intent.owner,
            wake_identity=intent.wake_identity,
            task_version=intent.task_version,
            idem_key=intent.idem_key,
            attempts=intent.attempts if attempts is None else attempts,
            state=new_state,
            assigned_at=intent.assigned_at,
        )
        self._append(updated)
        return updated

    def pending(self) -> list[OutboxIntent]:
        """Return all intents still awaiting delivery, oldest first."""
        return sorted(
            (intent for intent in self._intents.values() if intent.state == "pending"),
            key=lambda intent: intent.assigned_at,
        )


class DispatcherWorker:
    """Poll snapshots and dispatch ready tasks for one project, exactly once.

    Parameters
    ----------
    name : str, optional
        Connection identity. Defaults to ``<project>/dispatcher``.
    uri : str, optional
        Hub URI.
    project : str
        Exact project scope; only tasks and cards of this project qualify.
    token : str or None, optional
        Shared-secret token for a secured hub.
    interval : float, optional
        Seconds between passes (floored at 1); the singleton lease TTL is
        three intervals.
    once : bool, optional
        Run a single pass and exit.
    dry_run : bool, optional
        Print the plan without any mutation or wake.
    suggestion_ttl : float, optional
        Seconds before an un-claimed suggestion re-opens.
    capacity : int, optional
        Maximum active claims per seat.
    max_attempts : int, optional
        Wake retries per intent before abandonment.
    outbox_path : pathlib.Path or None, optional
        JSONL outbox location; defaults to
        ``~/.synapse/dispatch-outbox/<project>.jsonl``.
    agent_factory : Callable, optional
        Client factory, injectable for tests.
    ready_timeout : float, optional
        Seconds to await hub readiness per connection.
    response_timeout : float, optional
        Seconds to await each snapshot set per pass.
    clock : Callable, optional
        Wall-clock source; injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        project: str,
        name: str = "",
        uri: str | None = None,
        token: str | None = None,
        interval: float = 60.0,
        once: bool = False,
        dry_run: bool = False,
        suggestion_ttl: float = 900.0,
        capacity: int = 1,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        outbox_path: Path | None = None,
        agent_factory: Callable[..., SynapseAgent] = SynapseAgent,
        ready_timeout: float = 5.0,
        response_timeout: float = 3.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not project.strip():
            raise ValueError("a dispatcher requires an exact --project scope")
        self.project = project.strip()
        self.name = name.strip() or f"{self.project}/dispatcher"
        self.uri = uri or default_hub_uri()
        self.token = token
        self.interval = max(1.0, float(interval))
        self.once = bool(once)
        self.dry_run = bool(dry_run)
        self.suggestion_ttl = float(suggestion_ttl)
        self.capacity = max(1, int(capacity))
        self.max_attempts = max(1, int(max_attempts))
        self.outbox = DispatchOutbox(
            outbox_path or (Path.home() / ".synapse" / "dispatch-outbox" / f"{self.project}.jsonl")
        )
        self.agent_factory = agent_factory
        self.ready_timeout = float(ready_timeout)
        self.response_timeout = float(response_timeout)
        self.clock = clock

    async def run(self) -> int:
        """Connect and dispatch until interrupted (or one pass with --once)."""
        inbox: dict[str, dict[str, Any]] = {}

        async def collect(data: dict[str, Any]) -> None:
            inbox[str(data.get("type", ""))] = data

        agent = self.agent_factory(
            self.name, collect, uri=self.uri, verbose=False, token=self.token
        )
        conn_task = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(timeout=self.ready_timeout):
                print(f"[{self.name}] hub unreachable at {self.uri}")
                return 1
            while True:
                # A dry run is read-only by contract: plan, never hold the lease.
                if not self.dry_run and not await self._hold_lease(agent, inbox):
                    print(
                        f"[{self.name}] dispatch lease for {self.project} held elsewhere; yielding."
                    )
                    return 3
                snapshots = await self._fetch_snapshots(agent, inbox)
                if snapshots is None:
                    print(f"[{self.name}] snapshot fetch incomplete; retrying next pass.")
                else:
                    await self._pass(agent, inbox, snapshots)
                if self.once:
                    return 0
                await asyncio.sleep(self.interval)
        finally:
            agent.running = False
            conn_task.cancel()

    async def _hold_lease(self, agent: SynapseAgent, inbox: dict[str, dict[str, Any]]) -> bool:
        """Claim or renew the singleton ``dispatch:<project>`` lease."""
        task_id = f"{LEASE_TASK_PREFIX}{self.project}"
        await agent.claim(task_id, note="dispatcher singleton", ttl_seconds=self.interval * 3)
        for _ in range(int(self.response_timeout * 20)):
            granted = inbox.pop(MessageType.CLAIM_GRANTED, None)
            if granted is not None:
                return True
            if inbox.pop(MessageType.CLAIM_DENIED, None) is not None:
                return False
            await asyncio.sleep(0.05)
        return False

    async def _fetch_snapshots(
        self, agent: SynapseAgent, inbox: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]] | None:
        """Fetch board, state, manifest, and who snapshots for one pass."""
        expected = {
            MessageType.BOARD_SNAPSHOT,
            MessageType.STATE_SNAPSHOT,
            MessageType.MANIFEST_SNAPSHOT,
            MessageType.WHO_SNAPSHOT,
        }
        for message_type in expected:
            inbox.pop(message_type, None)
        await agent.request_board()
        await agent.request_state()
        await agent.request_manifest()
        await agent.request_who()
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while not expected.issubset(inbox) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.025)
        if not expected.issubset(inbox):
            return None
        return {message_type: inbox[message_type] for message_type in expected}

    async def _pass(
        self,
        agent: SynapseAgent,
        inbox: dict[str, dict[str, Any]],
        snapshots: dict[str, dict[str, Any]],
    ) -> None:
        """Run one deterministic plan + execute cycle over fresh snapshots."""
        board = snapshots[MessageType.BOARD_SNAPSHOT].get("board", {})
        state = snapshots[MessageType.STATE_SNAPSHOT].get("snapshot", {})
        who = snapshots[MessageType.WHO_SNAPSHOT]
        manifest = snapshots[MessageType.MANIFEST_SNAPSHOT].get("manifest", [])
        tasks = board.get("tasks", []) if isinstance(board, Mapping) else []
        ready = frozenset(board.get("ready", [])) if isinstance(board, Mapping) else frozenset()
        claims = state.get("claims", []) if isinstance(state, Mapping) else []
        claims_list: list[Mapping[str, Any]] = (
            [
                {"task_id": task_id, "owner": str(claim.get("owner", ""))}
                for task_id, claim in (claims.items() if isinstance(claims, Mapping) else [])
                if isinstance(claim, Mapping)
            ]
            if isinstance(claims, Mapping)
            else []
        )
        online = frozenset(str(name) for name in who.get("online_agents", []))
        wake_capabilities = {
            str(name): str(capability)
            for name, capability in (who.get("wake_capabilities", {}) or {}).items()
        }
        now = self.clock()
        plan = plan_dispatches(
            tasks=tasks,
            ready_ids=ready,
            claims=claims_list,
            cards=manifest if isinstance(manifest, list) else [],
            online=online,
            wake_capabilities=wake_capabilities,
            project=self.project,
            now=now,
            suggestion_ttl=self.suggestion_ttl,
            capacity=self.capacity,
        )
        versions = {
            str(task.get("task_id")): task.get("version", 1)
            for task in tasks
            if isinstance(task, Mapping) and task.get("task_id")
        }
        if self.dry_run:
            for assignment in plan.assignments:
                print(
                    f"[dry-run] {assignment.task_id} -> {assignment.owner} "
                    f"({', '.join(assignment.reasons)})"
                )
            return
        await self._reconcile(agent, tasks, claims_list, now)
        for assignment in plan.assignments:
            await self._execute(agent, inbox, assignment, versions.get(assignment.task_id, 1), now)

    async def _await_cas_verdict(
        self, inbox: dict[str, dict[str, Any]], assignment: DispatchAssignment, version: int
    ) -> bool:
        """Await the hub's verdict on a suggestion CAS (fail-closed on timeout).

        Success is the update broadcast carrying THIS assignment's suggested
        owner for the task (a broadcast naming another owner is someone else's
        write, not our success); a hub ``error`` naming the task is the
        conflict. Anything else — timeout included — aborts the nudge.
        """
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while asyncio.get_running_loop().time() < deadline:
            updated = inbox.pop(MessageType.LEDGER_TASK_UPDATED, None)
            if updated is not None:
                task = updated.get("task", {})
                if (
                    str(task.get("task_id") or "") == assignment.task_id
                    and str(task.get("suggested_owner") or "") == assignment.owner
                ):
                    return True
            error = inbox.pop(MessageType.ERROR, None)
            if error is not None and f"'{assignment.task_id}'" in str(error.get("payload") or ""):
                return False
            await asyncio.sleep(0.025)
        return False

    async def _execute(
        self,
        agent: SynapseAgent,
        inbox: dict[str, dict[str, Any]],
        assignment: DispatchAssignment,
        task_version: Any,
        now: float,
    ) -> None:
        """CAS the suggestion and send the wake for one assignment.

        The wake fires only after the CAS is confirmed; a conflict (or a
        missing verdict) transitions the intent ``conflicted`` and the nudge
        is aborted, exactly as documented.
        """
        version = int(task_version) if isinstance(task_version, int) else 1
        intent = self.outbox.record_assignment(assignment, version, now=now)
        if intent.state != "pending":
            return
        inbox.pop(MessageType.LEDGER_TASK_UPDATED, None)
        inbox.pop(MessageType.ERROR, None)
        await agent.update_ledger_task(
            assignment.task_id,
            suggested_owner=assignment.owner,
            expected_version=version,
        )
        if not await self._await_cas_verdict(inbox, assignment, version):
            self.outbox.transition(intent, state="conflicted")
            await agent.post_progress(
                assignment.task_id,
                f"dispatch: CAS suggestion for {assignment.owner} refused or "
                f"unconfirmed (expected v{version}); nudge aborted",
            )
            return
        wake_text = (
            f"DISPATCH {assignment.task_id}: suggested owner {assignment.owner}. "
            "Claim the task and set it in_progress if you take it; update the board or "
            f"clear the suggestion otherwise. ({'; '.join(assignment.reasons)})"
        )
        await agent.send_message(
            MessageType.CHAT,
            target=assignment.wake_identity,
            payload=wake_text,
            idem_key=intent.idem_key,
        )
        self.outbox.transition(intent, attempts=intent.attempts + 1)

    async def _reconcile(
        self, agent: SynapseAgent, tasks: list[Any], claims: list[Mapping[str, Any]], now: float
    ) -> None:
        """Resolve pending intents against live board state (exactly-once)."""
        claimed = {claim["task_id"] for claim in claims}
        board_state = {
            str(task.get("task_id")): task
            for task in tasks
            if isinstance(task, Mapping) and task.get("task_id")
        }
        for intent in self.outbox.pending():
            task = board_state.get(intent.task_id)
            if intent.task_id in claimed:
                self.outbox.transition(intent, state="delivered")
                continue
            if (
                task is None
                or str(task.get("status") or "") != "open"
                or str(task.get("suggested_owner") or "") != intent.owner
            ):
                self.outbox.transition(intent, state="conflicted")
                continue
            if intent.attempts >= self.max_attempts:
                self.outbox.transition(intent, state="abandoned")
                await agent.post_progress(
                    intent.task_id,
                    f"dispatch: wake for {intent.owner} abandoned after "
                    f"{intent.attempts} attempts (idem {intent.idem_key})",
                )
                continue
            wake_text = (
                f"DISPATCH {intent.task_id}: suggested owner {intent.owner}. "
                "Claim the task and set it in_progress if you take it; update the board or "
                "clear the suggestion otherwise. (retry "
                f"{intent.attempts + 1}/{self.max_attempts}, idem {intent.idem_key})"
            )
            await agent.send_message(
                MessageType.CHAT,
                target=intent.wake_identity,
                payload=wake_text,
                idem_key=intent.idem_key,
            )
            self.outbox.transition(intent, attempts=intent.attempts + 1)
