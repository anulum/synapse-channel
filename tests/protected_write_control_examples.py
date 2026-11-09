# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual protected frontend process journeys
"""Own disposable same-UID frontend processes; never claim installed role custody."""

from __future__ import annotations

import asyncio
import itertools
import json
import multiprocessing
import os
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path

from websockets.asyncio.server import serve

from protected_write_authority_examples import AUTHOR_KEY, _grant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim
from synapse_channel.core.message_auth import MessageAuthKey, sign_frame
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_authority import assemble_protected_write_authority
from synapse_channel.core.protected_write_control_gateway import ProtectedWriteControlGateway
from synapse_channel.core.protected_write_control_policy import (
    ProtectedWriteControlPeer,
    ProtectedWriteControlPolicy,
)
from synapse_channel.core.protected_write_control_server import ProtectedWriteControlServer
from synapse_channel.core.protected_write_dispatch import ProtectedWriteDispatch
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_handler_binding import assemble_protected_write_dispatch
from synapse_channel.core.protected_write_json import ProtectedWriteJsonLimits
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from test_protected_write_admission import _claim
from test_protected_write_effects import _plan
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request


def control_policy() -> ProtectedWriteControlPolicy:
    """Create explicit test policy caps independent of machine RAM or CPU count."""
    return ProtectedWriteControlPolicy(
        "continuity",
        "frontend-example",
        ProtectedWriteJsonLimits(131072, 8, 120000, 64),
        3.0,
        4,
        1048576,
    )


def native_peer() -> ProtectedWriteControlPeer:
    """Enroll this real process for diagnostic component connections."""
    return ProtectedWriteControlPeer(os.getpid(), os.geteuid(), os.getegid())


def _frontend(
    pipe: Connection,
    path: Path,
    policy: ProtectedWriteControlPolicy,
    authority_peer: ProtectedWriteControlPeer,
) -> None:
    async def run() -> None:
        gateway = ProtectedWriteControlGateway(path, authority_peer=authority_peer, policy=policy)
        async with serve(
            gateway,
            "127.0.0.1",
            0,
            max_size=65536,
            max_queue=1,
            open_timeout=policy.timeout_seconds,
            close_timeout=policy.timeout_seconds,
            ping_interval=None,
        ) as server:
            pipe.send(server.sockets[0].getsockname()[1])
            assert await asyncio.to_thread(pipe.recv) == "stop"

    try:
        asyncio.run(run())
    finally:
        pipe.close()


@asynccontextmanager
async def frontend_entry(
    ingress: ProtectedWriteTransport, directory: Path, *, frontend: bool = True
) -> AsyncIterator[str]:
    """Expose the same ingress directly or through an actual separately spawned relay."""
    if not frontend:
        async with serve(ingress, "127.0.0.1", 0, max_size=65536) as server:
            yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        return
    path = directory / "control.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    policy = control_policy()
    process = context.Process(target=_frontend, args=(child, path, policy, native_peer()))
    try:
        listener.bind(str(path))
        process.start()
        try:
            child.close()
            assert process.pid is not None
            handler = ProtectedWriteControlServer(
                ingress,
                frontend_peer=ProtectedWriteControlPeer(process.pid, os.geteuid(), os.getegid()),
                policy=policy,
            )
            async with handler.serve(listener):
                assert await asyncio.to_thread(parent.poll, 5)
                port = parent.recv()
                assert type(port) is int
                yield f"ws://127.0.0.1:{port}"
        finally:
            with suppress(BrokenPipeError):
                parent.send("stop")
            await asyncio.to_thread(process.join, 5)
            process.terminate()
            await asyncio.to_thread(process.join, 5)
            assert not process.is_alive()
            assert process.exitcode == 0
    finally:
        process.close()
        parent.close()
        child.close()
        listener.close()
        path.unlink(missing_ok=True)


@asynccontextmanager
async def authority_control(
    ingress: ProtectedWriteTransport,
    directory: Path,
    *,
    policy: ProtectedWriteControlPolicy | None = None,
    peer: ProtectedWriteControlPeer | None = None,
) -> AsyncIterator[tuple[Path, ProtectedWriteControlServer]]:
    """Own an actual native component listener and all its authority handlers."""
    path = directory / "component.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        handler = ProtectedWriteControlServer(
            ingress, frontend_peer=peer or native_peer(), policy=policy or control_policy()
        )
        async with handler.serve(listener):
            yield path, handler
    finally:
        listener.close()
        path.unlink(missing_ok=True)


@dataclass(frozen=True)
class ControlAuthorityExample:
    """Actual seven-handler authority with an executing reservation and live enrollment."""

    ingress: ProtectedWriteTransport
    control: ProtectedEnrollmentControl
    store: EventStore
    hub: SynapseHub
    request: dict[str, object]
    key: MessageAuthKey
    authority_key: MessageAuthKey
    operator_key: MessageAuthKey
    now: float
    dispatch: ProtectedWriteDispatch
    replay: DurableMessageAuthReplayStore

    def signed(self, number: int, **changes: object) -> str:
        """Sign a distinct original status request with the real client key."""
        frame = {**self.request, "request_id": f"control-status-{number}", **changes}
        return json.dumps(
            sign_frame(
                frame,
                key=self.key,
                nonce=f"control-{number}",
                sequence=number + 1,
                timestamp=self.now,
            )
        )

    def revoke(self, number: int) -> str:
        """Sign an actual executing-reservation revocation with the operator key."""
        frame = {
            **self.request,
            "request_id": f"control-revoke-{number}",
            "type": "protected_write_revoke",
            "sender": "EXAMPLE/operator",
            "session_id": "control-operator",
        }
        return json.dumps(
            sign_frame(
                frame,
                key=self.operator_key,
                nonce=f"control-revoke-{number}",
                sequence=number,
                timestamp=self.now,
            )
        )


@asynccontextmanager
async def control_authority(tmp_path: Path) -> AsyncIterator[ControlAuthorityExample]:
    """Retain real hub, enrollment, handler, replay and durable journal objects."""
    now = time.time()
    store = EventStore(tmp_path / "control-authority.db")
    with DurableMessageAuthReplayStore(
        tmp_path / "control-nonces.db", max_entries=64, window_seconds=10
    ) as replay:
        try:
            policy = ProtectedAdmissionReplayPolicy(
                LIMITS, frozenset(), 10, verify_recovery=lambda _parent, _child: False
            )
            claim = _claim()
            claim.lease_expires_at = now + 60
            claim.worktree = str(tmp_path)
            record_claim(store, claim)
            hub = SynapseHub(
                journal=store,
                protected_write_policies={"enrollment": policy},
                anti_rollback_checkpoint=False,
            )
            proposal = _plan()
            proposal["claims"][0]["lease_expires_at"] = claim.lease_expires_at
            digest = parse_protected_write_proposal(
                json.dumps(proposal), limits=LIMITS
            ).proposal_sha256
            key = MessageAuthKey("control-writer", b"w" * 32, frozenset({"EXAMPLE/writer"}))
            authority_key = MessageAuthKey(
                "control-authority", b"a" * 32, frozenset({"EXAMPLE/authority"})
            )
            grant = _grant(
                session_id="writer-session",
                principal="EXAMPLE/writer",
                key=key,
                digest=digest,
                now=now,
                verbs=frozenset({"begin", "settle", "status"}),
                incarnation="incarnation",
            )
            author = _grant(
                session_id="author-session",
                principal="EXAMPLE/author",
                key=AUTHOR_KEY,
                digest=digest,
                now=now,
                verbs=frozenset({"admit"}),
            )
            operator_key = MessageAuthKey(
                "control-operator", b"o" * 32, frozenset({"EXAMPLE/operator"})
            )
            operator = replace(
                grant,
                session_id="control-operator",
                principal="EXAMPLE/operator",
                key=operator_key,
                verbs=frozenset({"revoke"}),
                writer_incarnation=None,
            )
            numbers = itertools.count(1)
            root = tmp_path.stat()
            effects = ProtectedPreparationPolicy(
                "enrollment",
                LIMITS,
                {"memory": (root.st_dev, root.st_ino)},
                {("memory", "records/note.md"): frozenset({"task-1"})},
                {
                    ("memory", "records/note.md"): frozenset({"create", "fsync"}),
                    ("memory", "records"): frozenset({"fsync"}),
                },
                {("memory", "records/note.md"): ("memory", "records")},
                {},
                65536,
            )

            def sign(frame: dict[str, object]) -> dict[str, object]:
                number = next(numbers)
                return sign_frame(
                    frame,
                    key=authority_key,
                    nonce=f"control-reply-{number}",
                    sequence=number,
                    timestamp=now + 1,
                )

            authority = assemble_protected_write_authority(
                hub=hub,
                sealed_keys={item.session_id: item.key for item in (author, grant, operator)},
                effects=effects,
                replay_policy=policy,
                writer=(grant.principal, "incarnation"),
                operator_principals=frozenset({"EXAMPLE/operator"}),
                recovery_principals=frozenset({"EXAMPLE/recovery"}),
                clock=lambda: now + 1,
                sign_response=sign,
                replay_store=replay,
                sequence_floor_mode=SequenceFloorMode.STRICT,
            )
            await authority.operator_control.issue(grant)
            await authority.operator_control.issue(operator)
            await authority.operator_control.issue(author)
            admission = _request("admit")
            admission.update(
                sender=author.principal,
                session_id=author.session_id,
                proposal_sha256=digest,
                timestamp=now + 1,
                body={"proposal": proposal},
            )
            admitted = json.loads(
                await authority.ingress.open_session().exchange(
                    json.dumps(
                        sign_frame(
                            admission,
                            key=author.key,
                            nonce="control-admit",
                            sequence=1,
                            timestamp=now + 1,
                        )
                    )
                )
            )
            reservation = admitted["body"]["reservation_id"]
            begin = _request("begin")
            begin.update(
                sender=grant.principal,
                session_id=grant.session_id,
                proposal_sha256=digest,
                timestamp=now + 1,
                body={"reservation_id": reservation, "writer_incarnation": "incarnation"},
            )
            begun = json.loads(
                await authority.ingress.open_session().exchange(
                    json.dumps(
                        sign_frame(
                            begin,
                            key=grant.key,
                            nonce="control-begin",
                            sequence=1,
                            timestamp=now + 1,
                        )
                    )
                )
            )
            assert begun["body"]["disposition"] == "accepted"
            request = {
                **begin,
                "type": "protected_write_status",
                "body": {
                    "reservation_id": reservation,
                },
            }
            dispatch = assemble_protected_write_dispatch(
                hub=hub,
                effects=effects,
                replay_policy=policy,
                current_enrollments=authority.operator_control.current,
                writer=(grant.principal, "incarnation"),
                operator_principals=frozenset({"EXAMPLE/operator"}),
                recovery_principals=frozenset({"EXAMPLE/recovery"}),
                clock=lambda: now + 1,
                sign_response=sign,
            )
            yield ControlAuthorityExample(
                authority.ingress,
                authority.operator_control,
                store,
                hub,
                request,
                key,
                authority_key,
                operator_key,
                now + 1,
                dispatch,
                replay,
            )
        finally:
            store.close()
