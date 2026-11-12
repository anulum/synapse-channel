# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — reproducible benchmark of injected faults and their external effects
"""Measure what happens to the outside world when agents meet faults, mode by mode.

* **Agents** are scripted and deterministic; a seed fixes the tasks, their order and
  the fault each one meets. No model is called and nothing is spent.
* **The effect** of a task is one file change committed in the worker's own Git
  worktree together with one append to an external effect ledger (non-idempotent, like
  a payment or a message). Observers that are not the agents judge it: the ledger
  counts appends, the repository is read back by content digest.
* **Order.** A task first prepares its change; the scheduled fault strikes; only then
  is the effect made. In Core the agent checks its claim right before each attempt, so
  a task taken away before that point leaves no effect at all.
* **Baseline** is the honest plain set-up: every agent works in its own worktree and,
  like an ordinary script, retries an operation that errored or did not finish.
* **Core** runs the same agents against a real in-process hub: each claims its task,
  declares the operation (fenced by the claim's epoch) before any effect, checks the
  claim by an epoch-bound renewal before each attempt, and after a fault asks the
  effect's observer whether the effect happened instead of retrying blindly. An
  outcome the observer cannot establish is recorded on the hub as unknown and routed to
  an operator. Outcome and independent verification records for tasks that complete
  normally are not part of this mode yet.
* **Faults**: ``lost_before`` (the connection drops before the effect is made),
  ``lost_after`` (the effect is made, the answer is lost), ``restart`` (the agent dies
  after the effect, before it records completion, and starts again),
  ``duplicate_request`` (the same request is delivered again, to another worker, after
  the first one finished), ``withdrawn_authority`` (an operator takes the task back after
  the agent prepared its change), ``lease_expiry`` (the agent is cut off past its lease
  window after preparing the change; the task passes to another worker before it comes
  back) and ``unknown_outcome`` (the effect is made, the answer is lost, and the
  observer cannot answer an inquiry either).
* In Core the operator acts through the hub's governed operator relay, a peer principal
  the hub's serving policy trusts for ``release`` in the agents' namespace; the peer
  certificate is handed to the serving policy directly (its ``cert_source``), as Core's
  own relay tests do, so the TLS handshake is not part of this measurement. The lease
  lapses through the hub's holder-offline window, shortened here to
  :data:`LEASE_WINDOW`; the lease TTL has a 30-second floor and ends in the same state.
  A release that lands between the claim check and the effect is not caught unless the
  effect target itself checks the claim's epoch; the scheduled faults strike before it.

Authority is judged by the harness, not the agents: a register names the worker each
task belongs to at every moment, and every effect is recorded with whether its worker
held the task then.

Measures per mode, overall and per fault class: unauthorised, duplicate and lost
effects, verified task success (the intended effect exactly once, by the worker the
task belonged to, with the file's digest), human interventions, latency per completed
task (``None`` when none completed) beside the latency of all attempts, recovery time
(fault to completion or correct abandonment), and CPU seconds per completed task
counted for this process and the child processes it waited for (Git), where the
platform reports child CPU. Two runs with the same seed give identical counts. The
result names the Python, the Core version, path and source commit actually loaded, and
the SHA-256 of this file.

Run with ``python benchmarks/fault_effects_benchmark.py --seed 7``; the JSON result
goes to stdout or ``--results FILE``. Results are shown to the owner before any
publication.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import hashlib
import json
import os
import platform
import random
import secrets
import socket
import subprocess  # nosec B404 - fixed git commands in a disposable repository
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import synapse_channel
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import relay_operator_action
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin_from_der,
)

FAULTS = (
    "none",
    "lost_before",
    "lost_after",
    "restart",
    "duplicate_request",
    "withdrawn_authority",
    "lease_expiry",
    "unknown_outcome",
)
"""Fault classes measured; ``none`` is the control."""

MAX_ATTEMPTS = 3
"""How often an agent tries one operation before it gives up."""

LEASE_WINDOW = 0.3
"""Seconds the benchmark hub keeps a disconnected holder's claims (its holder-offline window)."""

CUT_OFF = 0.5
"""Seconds a ``lease_expiry`` agent stays cut off; longer than :data:`LEASE_WINDOW`."""

RIVAL = "agent-rival"
"""The worker a ``lease_expiry`` task passes to while its agent is cut off."""

OTHER_WORKER = "agent-other"
"""The worker a ``duplicate_request`` task is delivered to the second time."""

HUB_ID = "bench-hub"
"""Identity of the benchmark hub, which owns the agents' namespace."""

NAMESPACE = "BENCH"
"""Project namespace of every benchmark agent."""

OPERATOR = "bench-operator"
"""The operator principal that may take a task back through the governed relay."""

OPERATOR_DOMAIN = "bench-operators"
"""Trust domain of :data:`OPERATOR` in the hub's serving policy."""

OPERATOR_KEY = "BENCH:operator:1"
"""Signing key id the serving policy binds to :data:`OPERATOR`."""


class LostConnection(RuntimeError):
    """The connection to the effect target dropped; the agent cannot tell what happened."""


class AgentCrashed(RuntimeError):
    """The agent process died after making the effect and before recording it."""


class ObserverUnavailable(RuntimeError):
    """The effect's observer cannot answer an inquiry; the outcome stays unknown."""


@dataclass(frozen=True)
class Task:
    """One unit of work: one effect (file commit and ledger append) and its scheduled fault."""

    task_id: str
    agent: str
    fault: str

    @property
    def operation_id(self) -> str:
        """Return the operation id the ledger records the effect under."""
        return f"op-{self.task_id}"

    @property
    def content(self) -> str:
        """Return the file content this task must leave in the agent's worktree."""
        return f"{self.task_id} done by {self.agent}\n"

    @property
    def path(self) -> str:
        """Return the worktree path of the task's file; its claim covers only this path."""
        return f"tasks/{self.task_id}.txt"


def schedule(seed: int, *, agents: int = 3, tasks_per_agent: int = len(FAULTS)) -> tuple[Task, ...]:
    """Return the seeded task list; by default every agent meets every fault class once.

    Parameters
    ----------
    seed : int
        Fixes the order of tasks and the fault each one meets.
    agents : int, optional
        Number of scripted agents.
    tasks_per_agent : int, optional
        Tasks per agent.

    Returns
    -------
    tuple[Task, ...]
        Tasks in execution order.
    """
    rng = random.Random(seed)  # nosec B311 - reproducible schedule, not cryptography
    tasks: list[Task] = []
    for agent_index in range(agents):
        for index in range(tasks_per_agent):
            fault = FAULTS[(agent_index + index) % len(FAULTS)]
            tasks.append(Task(f"t{agent_index}-{index}", f"agent-{agent_index}", fault))
    rng.shuffle(tasks)
    return tuple(tasks)


class Authority:
    """The harness's register of which worker each task belongs to; agents never write it."""

    def __init__(self) -> None:
        self._holder: dict[str, str] = {}

    def assign(self, task_id: str, worker: str) -> None:
        """Give the task to ``worker`` from now on."""
        self._holder[task_id] = worker

    def withdraw(self, task_id: str) -> None:
        """Take the task back from every worker; nobody holds it from now on."""
        self._holder[task_id] = ""

    def holds(self, task: Task, worker: str) -> bool:
        """Return whether ``worker`` holds ``task`` now; a task starts with its own agent."""
        return self._holder.get(task.task_id, task.agent) == worker


class EffectLedger:
    """An external, append-only effect target and its observer; it never trusts the agent."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.write_text("", encoding="utf-8")
        self.authority = Authority()

    def apply(self, task: Task, worker: str) -> None:
        """Append one record of the effect (non-idempotent, like a payment).

        The record notes whether ``worker`` held the task at that moment, by the
        harness's register, not by anything the worker says.
        """
        record = {
            "operation_id": task.operation_id,
            "agent": worker,
            "authorised": self.authority.holds(task, worker),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _records(self) -> list[dict[str, Any]]:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]

    def counts(self) -> Counter[str]:
        """Return how often each operation was applied, as the observer sees it."""
        return Counter(record["operation_id"] for record in self._records())

    def unauthorised(self) -> Counter[str]:
        """Return how often each operation was applied by a worker that did not hold it."""
        return Counter(r["operation_id"] for r in self._records() if not r["authorised"])


class Repository:
    """A fresh disposable Git repository with one worktree per agent."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)  # a fresh directory only, so no earlier run leaks in
        self._git(root, "init", "-q", "-b", "main")
        (root / "README").write_text("fault-effects benchmark\n", encoding="utf-8")
        self._git(root, "add", "README")
        self._git(root, "commit", "-q", "-m", "init")

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        command = ["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost", *args]
        result = subprocess.run(  # nosec B603 - fixed argument vector
            command, cwd=cwd, check=True, capture_output=True, text=True
        )
        return result.stdout

    def worktree(self, agent: str) -> Path:
        """Return the agent's own worktree, creating it on its own branch."""
        path = self.root.parent / f"wt-{agent}"
        if not path.exists():
            self._git(self.root, "worktree", "add", "-q", "-b", agent, str(path))
        return path

    def commit_file(self, agent: str, task_id: str, content: str) -> None:
        """Write and commit the task's file in the agent's worktree."""
        tree = self.worktree(agent)
        target = tree / "tasks" / f"{task_id}.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._git(tree, "add", str(target.relative_to(tree)))
        if self._git(tree, "status", "--porcelain"):
            self._git(tree, "commit", "-q", "-m", task_id)

    def digest(self, agent: str, task_id: str) -> str | None:
        """Return the committed file's SHA-256 on the agent's branch, or ``None``."""
        try:
            body = self._git(self.root, "show", f"{agent}:tasks/{task_id}.txt")
        except subprocess.CalledProcessError:
            return None
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class TaskRecord:
    """What happened to one task, as the observers judged it."""

    task_id: str
    fault: str
    attempts: int = 0
    gave_up: bool = False
    human: bool = False
    reason: str = ""
    latency: float = 0.0
    recovery: float | None = None
    effects: int = 0
    unauthorised: int = 0
    file_ok: bool = False
    cpu_seconds: float | None = None

    @property
    def verified(self) -> bool:
        """Return whether the intended effect happened once, by its holder, and the file matches."""
        return self.effects == 1 and self.unauthorised == 0 and self.file_ok


class FaultyTarget:
    """The outside world a task acts on, with the task's scheduled fault injected once."""

    def __init__(self, ledger: EffectLedger, repository: Repository) -> None:
        self.ledger = ledger
        self.repository = repository
        self._fired: set[str] = set()

    def apply(self, task: Task, worker: str) -> None:
        """Make ``worker``'s effect (file commit and record), or meet the fault once."""
        first = task.task_id not in self._fired
        self._fired.add(task.task_id)
        if first and task.fault == "lost_before":
            raise LostConnection(task.task_id)
        self.repository.commit_file(worker, task.task_id, task.content)
        self.ledger.apply(task, worker)
        if first and task.fault in ("lost_after", "unknown_outcome"):
            raise LostConnection(task.task_id)
        if first and task.fault == "restart":
            raise AgentCrashed(task.task_id)

    def inquire(self, task: Task) -> int:
        """Ask the effect's observer how often the task's operation was applied."""
        if task.fault == "unknown_outcome":
            raise ObserverUnavailable(task.task_id)
        return self.ledger.counts()[task.operation_id]


def run_baseline(
    tasks: tuple[Task, ...],
    workdir: Path,
    *,
    target_factory: type[FaultyTarget] | None = None,
) -> list[TaskRecord]:
    """Run every task the plain way: retry what errored, redo after a restart.

    Parameters
    ----------
    tasks : tuple[Task, ...]
        The seeded schedule.
    workdir : pathlib.Path
        An empty directory for the repository and the ledger.
    target_factory : type[FaultyTarget] or None, optional
        The effect target with its fault injection; :class:`FaultyTarget` by default.

    Returns
    -------
    list[TaskRecord]
        One record per task, judged by the observers.
    """
    repository = Repository(workdir / "repo")
    ledger = EffectLedger(workdir / "effects.jsonl")
    target = (target_factory or FaultyTarget)(ledger, repository)
    records: list[TaskRecord] = []
    for task in tasks:
        record = TaskRecord(task.task_id, task.fault)
        started, cpu = time.perf_counter(), _cpu_now()
        _plain_delivery(task, task.agent, target, record)
        if task.fault == "duplicate_request":
            # The queue hands the same request to another worker, which does it again.
            ledger.authority.assign(task.task_id, OTHER_WORKER)
            _plain_delivery(task, OTHER_WORKER, target, record)
        record.latency = time.perf_counter() - started
        record.cpu_seconds = _cpu_since(cpu)
        records.append(record)
    _judge(tasks, records, repository, ledger)
    return records


def _plain_delivery(task: Task, worker: str, target: FaultyTarget, record: TaskRecord) -> None:
    """Do one delivery of the task the plain way: retry what errored, redo after a restart."""
    fault_at: float | None = None
    done = False
    while not done and record.attempts < MAX_ATTEMPTS:
        record.attempts += 1
        try:
            # The change is prepared (its content is fixed); the fault strikes before the effect.
            if record.attempts == 1 and task.fault == "lease_expiry":
                # Cut off past its lease; the task passes on, but nothing tells this agent.
                time.sleep(CUT_OFF)
                target.ledger.authority.assign(task.task_id, RIVAL)
            elif record.attempts == 1 and task.fault == "withdrawn_authority":
                # An operator takes the task back; a plain script has no channel to hear it.
                target.ledger.authority.withdraw(task.task_id)
            target.apply(task, worker)
            done = True
        except (LostConnection, AgentCrashed):
            # A plain script cannot tell whether the effect happened; it tries again.
            fault_at = fault_at or time.perf_counter()
    record.gave_up = record.gave_up or not done
    if fault_at is not None and done:
        record.recovery = time.perf_counter() - fault_at


class _Session:
    """One connected agent identity that keeps the frames it is sent."""

    def __init__(self, name: str, uri: str) -> None:
        self.frames: list[dict[str, Any]] = []
        self.agent = SynapseAgent(
            name, self._collect, uri=uri, verbose=False, machine_identity=False
        )
        self._task: asyncio.Task[None] | None = None

    async def _collect(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def open(self, timeout: float = 5.0) -> _Session:
        """Connect and wait for the hub's welcome."""
        self._task = asyncio.create_task(self.agent.connect())
        if not await self.agent.wait_until_ready(timeout=timeout):
            raise RuntimeError(f"{self.agent.name} could not connect")
        return self

    async def close(self) -> None:
        """Disconnect, as a crashed or finished agent does."""
        self.agent.running = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _first(
        self, wanted: Callable[[dict[str, Any]], bool], *, since: int, rounds: int, what: str
    ) -> dict[str, Any]:
        """Return the first frame from index ``since`` that ``wanted`` accepts."""
        for _ in range(rounds):
            for frame in self.frames[since:]:
                if wanted(frame):
                    return frame
            await asyncio.sleep(0.01)
        raise TimeoutError(f"no {what} arrived")

    async def wait(
        self, kind: str, *, since: int = 0, rounds: int = 500, **match: object
    ) -> dict[str, Any]:
        """Return the first frame of ``kind`` from index ``since`` whose fields equal ``match``."""
        return await self._first(
            lambda frame: (
                frame.get("type") == kind and all(frame.get(k) == v for k, v in match.items())
            ),
            since=since,
            rounds=rounds,
            what=f"{kind} frame",
        )

    async def try_claim(self, task: Task, *, rounds: int = 500) -> int | None:
        """Claim the task; return the fencing epoch, or ``None`` when the hub refuses."""
        since = len(self.frames)
        await self.agent.claim(task.task_id, note="fault-effects benchmark", paths=[task.path])
        return await self._claim_answer(task, since, rounds)

    async def renew(self, task: Task, epoch: int, *, rounds: int = 500) -> int | None:
        """Renew the claim taken at ``epoch``; ``None`` when it is no longer this agent's.

        An epoch-bound renewal never acquires a lease that was released, expired or taken
        over, so it answers whether the agent still holds the task right now.
        """
        renewal = f"renew-{task.task_id}-{secrets.token_hex(8)}"
        since = len(self.frames)
        await self.agent.send_message(
            MessageType.CLAIM,
            target="System",
            payload=task.task_id,
            task_id=task.task_id,
            renew_epoch=epoch,
            renewal_id=renewal,
            idem_key=renewal,
            paths=[task.path],
        )
        return await self._claim_answer(task, since, rounds)

    async def _claim_answer(self, task: Task, since: int, rounds: int) -> int | None:
        name = self.agent.name

        def answer(frame: dict[str, Any]) -> bool:
            granted = frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("owner") == name
            denied = frame.get("type") == MessageType.CLAIM_DENIED
            return frame.get("task_id") == task.task_id and (granted or denied)

        frame = await self._first(
            answer, since=since, rounds=rounds, what=f"answer to the claim on {task.task_id}"
        )
        return int(frame["epoch"]) if frame.get("type") == MessageType.CLAIM_GRANTED else None

    async def claim(self, task: Task) -> int:
        """Claim the task and return the epoch that fences its operation; refuse a denial."""
        epoch = await self.try_claim(task)
        if epoch is None:
            raise RuntimeError(f"{self.agent.name} was refused {task.task_id}")
        return epoch

    async def _record(self, request: str, **fields: Any) -> tuple[str, str]:
        since = len(self.frames)
        await self.agent.send_message(
            MessageType.OPERATION_RECORD, target="System", request_id=request, **fields
        )
        result = await self.wait(
            MessageType.OPERATION_RECORD_RESULT, since=since, request_id=request
        )
        return str(result.get("status")), str(result.get("reason") or "")

    async def declare(self, task: Task, epoch: int) -> tuple[str, str]:
        """Declare the task's operation before any effect; return the hub's status and reason."""
        return await self._record(
            f"declare-{task.task_id}-{self.agent.name}",
            record="declare",
            task_id=task.task_id,
            operation_id=task.operation_id,
            epoch=epoch,
            effect="file",
            effect_target={
                "path": task.path,
                "sha256": hashlib.sha256(task.content.encode()).hexdigest(),
            },
        )

    async def report_unknown(self, task: Task) -> str:
        """Record that the outcome is unknown and needs an operator; return the hub's status."""
        report = ExecutionOutcome(
            task.operation_id,
            "worker_process",
            OutcomeState.UNKNOWN,
            "the effect target dropped and its observer cannot answer",
            external_effect=OutcomeState.UNKNOWN,
        ).to_json()
        status, _reason = await self._record(
            f"outcome-{task.task_id}",
            record="outcome",
            task_id=task.task_id,
            operation_id=task.operation_id,
            report=report,
        )
        return status

    async def release(self, task: Task, epoch: int) -> None:
        """Release the claim once the task is settled."""
        since = len(self.frames)
        await self.agent.release(task.task_id, epoch=epoch)
        await self.wait(MessageType.RELEASE_GRANTED, since=since, task_id=task.task_id)


@contextlib.asynccontextmanager
async def _hub(directory: Path) -> AsyncIterator[str]:
    """Serve an in-process hub with a journal; claims, messages and outcome records open to all.

    The hub releases a disconnected holder's claims after :data:`LEASE_WINDOW` seconds,
    at the next claim attempt, as it does after its configured window in production. It
    owns the agents' namespace and lets :data:`OPERATOR` release tasks in it through the
    governed operator relay.
    """
    store = EventStore(directory / "hub.db")
    policy = AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "agents claim their tasks"),
            AclRule(MESSAGE, "agent", "*", "", "agents talk to the hub"),
            AclRule(OPERATION_EVIDENCE, "claim", "*", "", "agents report their operations"),
        ]
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    hub = SynapseHub(
        hub_id=HUB_ID,
        journal=store,
        acl_policy=policy,
        lease_offline_ttl=LEASE_WINDOW,
        multihub_serving_policy=_operator_policy(),
        namespace_ownership=NamespaceOwnership(owners={NAMESPACE: HUB_ID}, local_hub_id=HUB_ID),
    )
    serving = asyncio.create_task(hub.serve("127.0.0.1", port))
    try:
        await _listening(port)
        yield f"ws://127.0.0.1:{port}"
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        store.close()


def _operator_policy() -> MultiHubServingPolicy:
    """Trust :data:`OPERATOR` (a fresh self-signed certificate) to relay ``release`` only."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, OPERATOR)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    pin = certificate_sha256_pin_from_der(der)
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=OPERATOR_DOMAIN,
                    namespaces=frozenset({NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({OPERATOR_KEY}),
                    scope_grants=(ScopeGrant(verb="release", namespace=NAMESPACE),),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                OPERATOR_DOMAIN: MTLSTrustedPeer(
                    peer_id=OPERATOR_DOMAIN,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({OPERATOR_KEY}),
                    projects=frozenset({NAMESPACE}),
                )
            }
        ),
        grants={OPERATOR: MultiHubServingGrant(OPERATOR_DOMAIN, NAMESPACE, OPERATOR_KEY)},
        clock=time.time,
        cert_source=lambda _websocket: der,
    )


async def _listening(port: int, rounds: int = 300) -> None:
    """Wait until something accepts connections on ``port``; refuse a hub that never does."""
    for _ in range(rounds):
        with contextlib.suppress(OSError):
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        await asyncio.sleep(0.01)
    raise RuntimeError(f"nothing listens on port {port}")


async def _core_task(task: Task, uri: str, target: FaultyTarget) -> TaskRecord:
    """Run one task the Core way, then its second delivery when the request is duplicated."""
    record = TaskRecord(task.task_id, task.fault)
    started, cpu = time.perf_counter(), _cpu_now()
    try:
        await _core_delivery(task, uri, target, record)
        if task.fault == "duplicate_request":
            target.ledger.authority.assign(task.task_id, OTHER_WORKER)
            await _core_redelivery(task, uri, target, record)
    finally:
        record.latency = time.perf_counter() - started
        record.cpu_seconds = _cpu_since(cpu)
    return record


async def _core_delivery(task: Task, uri: str, target: FaultyTarget, record: TaskRecord) -> None:
    """Claim, declare, check the claim before each attempt, act, and inquire after a fault."""
    name = f"BENCH/{task.agent}"
    session = await _Session(name, uri).open()
    try:
        claimed = await session.claim(task)
        status, reason = await session.declare(task, claimed)
        if status != "accepted":
            record.gave_up, record.reason = True, f"declaration {status}: {reason}"
            await session.release(task, claimed)
            return
        # The change is prepared; the scheduled fault strikes before any effect.
        fault_at: float | None = None
        if task.fault == "lease_expiry":
            fault_at = time.perf_counter()
            await session.close()  # cut off, past the hub's lease window
            await asyncio.sleep(CUT_OFF)
            async with _rival_holds(task, uri, target):
                session = await _Session(name, uri).open()
        elif task.fault == "withdrawn_authority":
            fault_at = time.perf_counter()
            await _operator_withdraws(task, uri, target)
        epoch: int | None = claimed
        while record.attempts < MAX_ATTEMPTS:
            epoch = await session.renew(task, claimed)  # still ours at the effect boundary?
            if epoch is None:
                break
            record.attempts += 1
            try:
                target.apply(task, task.agent)
                break
            except (LostConnection, AgentCrashed) as fault:
                fault_at = fault_at or time.perf_counter()
                if isinstance(fault, AgentCrashed):
                    # The process died: a new one connects as the same identity.
                    await session.close()
                    session = await _Session(name, uri).open()
                # Inquire, never retry blindly: the observer says whether the effect exists.
                try:
                    if target.inquire(task) > 0:
                        break
                except ObserverUnavailable:
                    status = await session.report_unknown(task)
                    record.human = True
                    record.reason = f"outcome unknown, recorded {status}; routed to an operator"
                    break
        else:
            record.gave_up, record.reason = True, "the effect stayed absent after every attempt"
        if epoch is None:
            record.gave_up, record.reason = True, "the claim check was refused; no effect made"
        if fault_at is not None and (epoch is None or not record.gave_up):
            record.recovery = time.perf_counter() - fault_at
        if epoch is not None:
            await session.release(task, epoch)
    finally:
        await session.close()


async def _operator_withdraws(task: Task, uri: str, target: FaultyTarget) -> None:
    """Take the task back through the hub's governed operator relay.

    The register records the withdrawal whatever the hub did: if the hub kept the
    claim, the agent's next check passes, it acts, and the observer counts that effect
    as unauthorised, so a relay that fails shows in the result instead of hiding.
    """
    await relay_operator_action(
        RelayActionRequest(
            action="release",
            namespace=NAMESPACE,
            task_id=task.task_id,
            operator=OPERATOR,
            origin_hub_id=OPERATOR_DOMAIN,
            reason="authority withdrawn during the task",
        ),
        uri=uri,
        local_id=OPERATOR,
    )
    target.ledger.authority.withdraw(task.task_id)


@contextlib.asynccontextmanager
async def _rival_holds(task: Task, uri: str, target: FaultyTarget) -> AsyncIterator[None]:
    """Let another worker take the lapsed task and hold it while the cut-off agent returns."""
    rival = await _Session(f"BENCH/{RIVAL}", uri).open()
    try:
        epoch = await rival.claim(task)  # the hub releases the lapsed claim first
        target.ledger.authority.assign(task.task_id, RIVAL)
        yield
        await rival.release(task, epoch)
    finally:
        await rival.close()


async def _core_redelivery(task: Task, uri: str, target: FaultyTarget, record: TaskRecord) -> None:
    """Deliver the same request to another worker; it acts only if its declaration is accepted."""
    session = await _Session(f"BENCH/{OTHER_WORKER}", uri).open()
    try:
        epoch = await session.claim(task)
        record.attempts += 1
        status, reason = await session.declare(task, epoch)
        if status == "accepted":
            target.apply(task, OTHER_WORKER)
        else:
            record.reason = f"second delivery {status}: {reason}"
        await session.release(task, epoch)
    finally:
        await session.close()


def run_core(
    tasks: tuple[Task, ...],
    workdir: Path,
    *,
    target_factory: type[FaultyTarget] | None = None,
) -> list[TaskRecord]:
    """Run every task against a real hub with claims, declarations and inquiry.

    Parameters
    ----------
    tasks : tuple[Task, ...]
        The seeded schedule.
    workdir : pathlib.Path
        An empty directory for the hub, the repository and the ledger.
    target_factory : type[FaultyTarget] or None, optional
        The effect target with its fault injection; :class:`FaultyTarget` by default.

    Returns
    -------
    list[TaskRecord]
        One record per task, judged by the observers.
    """
    repository = Repository(workdir / "repo")
    ledger = EffectLedger(workdir / "effects.jsonl")
    target = (target_factory or FaultyTarget)(ledger, repository)

    async def everything() -> list[TaskRecord]:
        async with _hub(workdir) as uri:
            return [await _core_task(task, uri, target) for task in tasks]

    records = asyncio.run(everything())
    _judge(tasks, records, repository, ledger)
    return records


def _judge(
    tasks: tuple[Task, ...],
    records: list[TaskRecord],
    repository: Repository,
    ledger: EffectLedger,
) -> None:
    """Let the observers, not the agents, decide what each task left behind."""
    counts, unauthorised = ledger.counts(), ledger.unauthorised()
    for task, record in zip(tasks, records, strict=True):
        record.effects = counts[task.operation_id]
        record.unauthorised = unauthorised[task.operation_id]
        digest = repository.digest(task.agent, task.task_id)
        record.file_ok = digest == hashlib.sha256(task.content.encode()).hexdigest()


@dataclass(frozen=True)
class ModeResult:
    """The measures of one mode."""

    mode: str
    records: tuple[TaskRecord, ...] = field(default_factory=tuple)

    def measures(self) -> dict[str, object]:
        """Return the counts and times of this mode, overall and per fault class."""
        by_fault = {
            fault: _measures([r for r in self.records if r.fault == fault]) for fault in FAULTS
        }
        return {"mode": self.mode, **_measures(list(self.records)), "by_fault": by_fault}


def _measures(records: list[TaskRecord]) -> dict[str, Any]:
    """Return the counts, the time populations and the cost of a set of task records.

    ``mean_completed_latency_seconds`` covers verified tasks only and is ``None`` when
    none completed; ``mean_attempt_latency_seconds`` covers every task. CPU is the work
    of all tasks (completed or not) divided by the completed ones, ``None`` when none
    completed or when the platform does not report child-process CPU.
    """
    completed = [r for r in records if r.verified]
    cpu = [r.cpu_seconds for r in records]
    cpu_known = bool(cpu) and None not in cpu
    return {
        **_counts(records),
        "mean_completed_latency_seconds": _mean([r.latency for r in completed]),
        "mean_attempt_latency_seconds": _mean([r.latency for r in records]),
        "mean_recovery_seconds": _mean([r.recovery for r in records if r.recovery is not None]),
        "cpu_seconds_per_completed_task": (
            sum(c for c in cpu if c is not None) / len(completed)
            if completed and cpu_known
            else None
        ),
    }


def _counts(records: list[TaskRecord]) -> dict[str, int]:
    """Return the effect counts of a set of task records."""
    return {
        "tasks": len(records),
        "verified_success": sum(1 for r in records if r.verified),
        "unauthorised_effects": sum(r.unauthorised for r in records),
        "duplicate_effects": sum(r.effects - 1 for r in records if r.effects > 1),
        "lost_effects": sum(1 for r in records if r.effects == 0),
        "gave_up": sum(1 for r in records if r.gave_up),
        "human_interventions": sum(1 for r in records if r.human),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


CHILD_CPU_AVAILABLE = os.name != "nt"
"""Whether the platform reports CPU of finished child processes (``os.times``)."""


def _cpu_now() -> tuple[float, float]:
    """Return CPU seconds so far of this process and of the children it waited for."""
    times = os.times()
    return times.user + times.system, times.children_user + times.children_system


def _cpu_since(start: tuple[float, float]) -> float | None:
    """Return CPU seconds since ``start``, children included, or ``None`` when not reported."""
    own, children = _cpu_now()
    total = (own - start[0]) + (children - start[1])
    return total if CHILD_CPU_AVAILABLE else None


def _git_output(directory: Path, *args: str) -> str:
    """Return a Git command's output in ``directory``, or ``""`` when it is not a checkout."""
    result = subprocess.run(  # nosec B603 B607 - fixed git read commands
        ["git", "-C", str(directory), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def runtime_identity() -> dict[str, object]:
    """Name what actually ran: Python, the Core version and source loaded, this file's digest.

    Returns
    -------
    dict
        ``python``, ``core_version``, ``core_path`` (the imported package directory),
        ``core_commit`` and ``core_uncommitted_changes`` (``None`` when Core is not
        loaded from a Git checkout), ``benchmark_sha256``, ``child_cpu_reported``, and
        ``fleet`` (not used by these modes).
    """
    core_path = Path(synapse_channel.__file__).resolve().parent
    commit = _git_output(core_path, "rev-parse", "HEAD") or None
    changes = _git_output(core_path, "status", "--porcelain", "--", ".")
    return {
        "python": platform.python_version(),
        "core_version": synapse_channel.__version__,
        "core_path": str(core_path),
        "core_commit": commit,
        "core_uncommitted_changes": bool(changes) if commit else None,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "child_cpu_reported": CHILD_CPU_AVAILABLE,
        "fleet": "not used by the baseline and Core modes",
    }


def run(seed: int, *, workdir: Path | None = None) -> dict[str, Any]:
    """Run every mode of this unit for ``seed`` and return the result document.

    Parameters
    ----------
    seed : int
        Fixes the schedule.
    workdir : pathlib.Path or None, optional
        Directory for the disposable repositories; a temporary one when omitted.

    Returns
    -------
    dict
        Seed, versions, host, schedule, and per-mode measures with per-task records.
    """
    tasks = schedule(seed)
    with tempfile.TemporaryDirectory() as scratch:
        base = workdir if workdir is not None else Path(scratch)
        baseline = run_baseline(tasks, base / "baseline")
        core_dir = base / "core"
        core_dir.mkdir(parents=True)
        core = run_core(tasks, core_dir)
    return {
        "benchmark": "fault_effects",
        "seed": seed,
        "versions": runtime_identity(),
        "host": {"machine": platform.machine(), "system": platform.system()},
        "schedule": [task.__dict__ for task in tasks],
        "modes": [
            ModeResult("baseline", tuple(baseline)).measures(),
            ModeResult("core", tuple(core)).measures(),
        ],
        "records": {
            mode: [record.__dict__ | {"verified": record.verified} for record in records]
            for mode, records in (("baseline", baseline), ("core", core))
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and print or save its JSON result."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--results", type=Path, default=None)
    args = parser.parse_args(argv)
    document = run(args.seed)
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.results is None:
        sys.stdout.write(text)
    else:
        args.results.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
