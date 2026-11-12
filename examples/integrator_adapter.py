# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a minimal integrator adapter over the six contract steps
"""Run one operation through the six steps of the integrator contract.

See ``docs/integrator-contract.md``. The adapter uses only the names that page
lists:

1. register the executor: connect as ``DEMO/worker``;
2. propose the operation: the provider declares ``SYN_OPERATION_ID`` on the task
   (``examples/integrator_provider.py``);
3. coordinate: claim the task and keep its epoch (a claim is not permission;
   the hub's grants to this identity are);
4. execute: ``run_worker_session`` starts the provider and writes its outcome report;
5. follow the result: an independent checker, granted ``operation-evidence``,
   publishes the outcome report and a file verification on the hub;
6. recover after an unknown: read the report again; ``unknown`` means inquire,
   never retry automatically.

``python examples/integrator_adapter.py`` starts its own in-process hub and prints
``success: integrator adapter completed`` when every step was accepted.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.result_verification import read_outcome, verify_file_change
from synapse_channel.worker_outcome import read_worker_outcome
from synapse_channel.worker_session import run_worker_session

WORKER = "DEMO/worker"
CHECKER = "CHECK/checker"
TASK = "DEMO/task-1"
PROVIDER = Path(__file__).with_name("integrator_provider.py")


@dataclass(frozen=True)
class AdapterRun:
    """What each contract step reported."""

    operation_id: str
    provider_exit: int
    outcome_state: str
    outcome_record: str
    verification_result: str
    verification_record: str
    released: bool
    inquiry_state: str


class _Session:
    """One connected identity that collects the frames it is sent."""

    _task: asyncio.Task[None]

    def __init__(self, name: str, uri: str) -> None:
        self.frames: list[dict[str, Any]] = []
        self.agent = SynapseAgent(name, self._collect, uri=uri, verbose=False)

    async def _collect(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def __aenter__(self) -> _Session:
        """Connect, and raise once the hub has not welcomed this identity.

        The connect task is cancelled before the refusal is reported. Entering
        a context does not run the exit method when this method raises, so the
        task would otherwise keep running after the caller has already stopped.
        """
        self._task = asyncio.create_task(self.agent.connect())
        if await self.agent.wait_until_ready(timeout=5.0):
            return self
        await self.__aexit__(None, None, None)
        raise RuntimeError(f"{self.agent.name} could not connect")

    async def __aexit__(self, *_exc: object) -> None:
        """Cancel the connect task started when the session was entered."""
        self.agent.running = False
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def wait(self, kind: str, **match: object) -> dict[str, Any]:
        """Return the first frame of ``kind`` whose fields equal ``match``."""
        for _ in range(500):
            for frame in self.frames:
                if frame.get("type") == kind and all(frame.get(k) == v for k, v in match.items()):
                    return frame
            await asyncio.sleep(0.01)
        raise TimeoutError(f"no {kind} frame arrived")

    async def record(self, request_id: str, **fields: Any) -> str:
        """Send one ``operation_record`` and return the hub's status for it."""
        await self.agent.send_message(
            MessageType.OPERATION_RECORD, target="System", request_id=request_id, **fields
        )
        result = await self.wait(MessageType.OPERATION_RECORD_RESULT, request_id=request_id)
        return str(result.get("status"))


async def run_adapter(uri: str, workdir: Path, *, text: str = "hello, contract\n") -> AdapterRun:
    """Carry one file write through the six steps against the hub at ``uri``."""
    artefact = workdir / "artefact.txt"
    report = workdir / "run-001.json"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    async with _Session(WORKER, uri) as worker:  # steps 1 and 3
        await worker.agent.claim(TASK, note="integrator adapter", paths=[str(artefact)])
        grant = await worker.wait(MessageType.CLAIM_GRANTED, task_id=TASK)
        epoch = int(grant["epoch"])
    command = [sys.executable, str(PROVIDER), "--uri", uri, "--identity", WORKER]
    command += ["--task", TASK, "--epoch", str(epoch), "--path", str(artefact), "--text", text]
    provider_exit = await asyncio.to_thread(  # steps 2 and 4
        run_worker_session,
        identity=WORKER,
        command=command,
        uri=uri,
        arm=False,
        terminal_tmux="off",
        cwd=workdir,
        outcome_file=report,
    )
    operation_id, state = read_outcome(report)
    verification = verify_file_change(
        artefact, digest, operation_id=operation_id, communication=state
    )
    async with _Session(CHECKER, uri) as checker:  # step 5
        outcome_record = await checker.record(
            "outcome-1",
            record="outcome",
            task_id=TASK,
            operation_id=operation_id,
            report=dict(read_worker_outcome(report)),
        )
        verification_record = await checker.record(
            "verification-1",
            record="verification",
            task_id=TASK,
            operation_id=operation_id,
            verification=verification.to_json(),
        )
    async with _Session(WORKER, uri) as worker:
        await worker.agent.release(TASK, epoch=epoch)
        released = await worker.wait(MessageType.RELEASE_GRANTED, task_id=TASK)
    _operation, inquiry_state = read_outcome(report)  # step 6: read again, never retry
    return AdapterRun(
        operation_id=operation_id,
        provider_exit=int(provider_exit),
        outcome_state=state,
        outcome_record=outcome_record,
        verification_result=verification.result,
        verification_record=verification_record,
        released=bool(released),
        inquiry_state=inquiry_state,
    )


def demo_policy() -> AclPolicy:
    """Return the hub policy the demo needs: claims and messages for all, evidence for CHECK."""
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "anyone may claim"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
            AclRule(OPERATION_EVIDENCE, "claim", "DEMO*", "CHECK", "the checker records evidence"),
        ]
    )


@contextlib.asynccontextmanager
async def demo_hub(directory: Path) -> AsyncIterator[str]:
    """Serve an in-process hub on a kernel-assigned port and yield its URI.

    The hub's own readiness is the signal that the port accepts connections.
    A hub that never becomes ready raises ``TimeoutError`` instead of handing
    out an address.
    """
    store = EventStore(directory / "hub.db")
    hub = SynapseHub(journal=store, acl_policy=demo_policy())
    serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        _host, port = await hub.wait_until_serving()
        yield f"ws://127.0.0.1:{port}"
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        store.close()


async def _main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        async with demo_hub(Path(directory)) as uri:
            run = await run_adapter(uri, Path(directory))
    accepted = (run.outcome_record, run.verification_record) == ("accepted", "accepted")
    if run.provider_exit == 0 and accepted and run.verification_result == "verified":
        print("success: integrator adapter completed")
        return 0
    print(f"integrator adapter stopped: {run}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
