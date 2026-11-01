# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real writer-side current authority status
"""Exercise current status over a signed socket and durable authority actor."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.server import serve, unix_serve
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import replay
from synapse_channel.core.message_auth import MessageAuthKey, MessageReplayCache, sign_frame
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_current_status import ProtectedWriteCurrentStatus
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence
from synapse_channel.core.protected_write_session_auth import (
    ProtectedSessionEnrollment,
    authenticate_protected_request,
)
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transition_handler import ProtectedWriteTransitionHandler
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICIES, POLICY
from test_protected_write_preparation import _begun


@asynccontextmanager
async def _status_exchange(
    ingress: ProtectedWriteTransport,
    socket_directory: Path,
    max_wire_bytes: int,
    transport: str,
) -> AsyncIterator[Callable[[bytes], bytes]]:
    """Run the real status protocol over its native Unix and TCP embeddings."""
    socket_path = socket_directory / "authority.sock"
    listener = (
        unix_serve(ingress, path=socket_path, max_size=max_wire_bytes)
        if transport == "unix"
        else serve(ingress, "127.0.0.1", 0, max_size=max_wire_bytes)
    )
    async with listener as server:
        if transport == "unix":
            socket_path.chmod(0o700)
            socket_stat = socket_path.lstat()
            parent_stat = socket_directory.stat()
            yield ProtectedWriteUnixExchange(
                socket_path,
                parent_identity=(parent_stat.st_dev, parent_stat.st_ino),
                parent_uid=os.geteuid(),
                parent_gid=parent_stat.st_gid,
                parent_mode=parent_stat.st_mode & 0o777,
                socket_identity=(socket_stat.st_dev, socket_stat.st_ino),
                socket_uid=os.geteuid(),
                socket_gid=socket_stat.st_gid,
                socket_mode=socket_stat.st_mode & 0o777,
                peer_uid=os.geteuid(),
                max_wire_bytes=max_wire_bytes,
                timeout_seconds=2.0,
            )
        else:
            port = server.sockets[0].getsockname()[1]

            def exchange(request: bytes) -> bytes:
                """Exchange bounded binary frames with the actual child authority server."""
                with connect(
                    f"ws://127.0.0.1:{port}",
                    open_timeout=2,
                    close_timeout=2,
                    max_size=max_wire_bytes,
                    ping_interval=None,
                ) as connection:
                    connection.send(request)
                    response = connection.recv(timeout=2)
                    assert type(response) is bytes
                    return response

            yield exchange


@pytest.mark.parametrize(
    "transport", [pytest.param("unix", marks=requires_linux_peer_credentials), "tcp"]
)
@pytest.mark.asyncio
async def test_writer_checks_fresh_signed_actor_status_and_revoked_session(
    tmp_path: Path, private_unix_directory: Path, transport: str
) -> None:
    """A real socket proves current begin; durable revocation refuses the next read."""
    store, prepared, _state = _begun(tmp_path)
    root_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        root = tmp_path.stat()
        sequence = ProtectedWriterSequence(
            root_descriptor,
            root_identity=(root.st_dev, root.st_ino),
            name="writer.sequence",
            owner_uid=os.geteuid(),
            key_id="current-status-writer",
            sender="EXAMPLE/writer",
            create=True,
        )
    finally:
        os.close(root_descriptor)
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "status-replay.db", max_entries=32, window_seconds=10
    )
    try:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        begun = hub.state.protected_write_reservations[prepared.admission.reservation_id]
        source = json.loads(begun.request_bytes)
        writer_key = MessageAuthKey(
            "current-status-writer", b"w" * 32, frozenset({"EXAMPLE/writer"})
        )
        authority_key = MessageAuthKey(
            "current-status-authority", b"a" * 32, frozenset({"EXAMPLE/authority"})
        )
        enrollment = ProtectedSessionEnrollment(
            session_id=source["session_id"],
            principal=source["sender"],
            target=source["target"],
            authority_id=source["authority_id"],
            authority_continuity=source["authority_continuity"],
            enrollment_revision=source["enrollment_revision"],
            transaction_id=source["transaction_id"],
            proposal_sha256=source["proposal_sha256"],
            verbs=frozenset({"begin", "status"}),
            expires_at=CONTEXT.now + 20,
            key=writer_key,
            writer_incarnation="incarnation",
        )
        operator_key = MessageAuthKey(
            "current-status-operator", b"r" * 32, frozenset({"EXAMPLE/operator"})
        )
        operator = replace(
            enrollment,
            session_id="operator-session",
            principal="EXAMPLE/operator",
            key=operator_key,
            verbs=frozenset({"revoke"}),
            writer_incarnation=None,
        )
        control = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={enrollment.session_id: writer_key, operator.session_id: operator_key},
            clock=lambda: CONTEXT.now + 1,
        )
        await control.issue(enrollment)
        await control.issue(operator)
        reply_numbers = itertools.count(1)

        def sign(frame: dict[str, object]) -> dict[str, object]:
            """Sign status replies with the retained authority key and fresh response sequence."""
            number = next(reply_numbers)
            return sign_frame(
                frame,
                key=authority_key,
                nonce=f"current-reply-{number}",
                sequence=number,
                timestamp=CONTEXT.now + 1,
            )

        status = ProtectedWriteStatus(
            hub=hub,
            limits=prepared.policy.limits,
            current_enrollments=control.current,
            clock=lambda: CONTEXT.now + 1,
            sign_response=sign,
            reason_codes=POLICY.reason_codes,
            max_reservations=POLICY.max_reservations,
        )
        ingress = ProtectedWriteTransport(
            limits=prepared.policy.limits,
            current_enrollments=control.current,
            replay_store=replay_store,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: CONTEXT.now + 1,
            dispatch=status,
            reason_codes=POLICY.reason_codes,
        )
        async with _status_exchange(
            ingress,
            private_unix_directory,
            prepared.policy.limits.json_limits.max_wire_bytes,
            transport,
        ) as exchange:
            admission_operation = store.get_operation(begun.admission.operation_key)
            assert admission_operation is not None
            admitted = replay(
                store,
                protected_write_policies=POLICIES,
                up_to_seq=admission_operation.commit_seq,
                now=CONTEXT.now + 1,
            ).state.protected_write_reservations[begun.admission.reservation_id]
            with pytest.raises(ValueError, match="exact enrolled writer begin"):
                ProtectedWriteCurrentStatus(
                    begun=admitted,
                    enrollment=enrollment,
                    authority_key=authority_key,
                    limits=prepared.policy.limits,
                    reason_codes=POLICY.reason_codes,
                    exchange=exchange,
                    next_sequence=sequence.next,
                    clock=lambda: CONTEXT.now + 1,
                    response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
                )
            with pytest.raises(ValueError, match="exact enrolled writer begin"):
                ProtectedWriteCurrentStatus(
                    begun=begun,
                    enrollment=operator,
                    authority_key=authority_key,
                    limits=prepared.policy.limits,
                    reason_codes=POLICY.reason_codes,
                    exchange=exchange,
                    next_sequence=sequence.next,
                    clock=lambda: CONTEXT.now + 1,
                    response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
                )
            client = ProtectedWriteCurrentStatus(
                begun=begun,
                enrollment=enrollment,
                authority_key=authority_key,
                limits=prepared.policy.limits,
                reason_codes=POLICY.reason_codes,
                exchange=exchange,
                next_sequence=sequence.next,
                clock=lambda: CONTEXT.now + 1,
                response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
            )
            assert await asyncio.to_thread(client) == begun
            custody = hub.state.protected_claim_custody.pop(begun.admission.reservation_id)
            try:
                with pytest.raises(ConnectionClosed):
                    await asyncio.to_thread(client)
            finally:
                hub.state.protected_claim_custody[begun.admission.reservation_id] = custody
            wrong_authority = ProtectedWriteCurrentStatus(
                begun=begun,
                enrollment=enrollment,
                authority_key=MessageAuthKey(
                    authority_key.key_id, b"not-the-authority-key", authority_key.senders
                ),
                limits=prepared.policy.limits,
                reason_codes=POLICY.reason_codes,
                exchange=exchange,
                next_sequence=sequence.next,
                clock=lambda: CONTEXT.now + 1,
                response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
            )
            with pytest.raises(ValueError, match="signature refused"):
                await asyncio.to_thread(wrong_authority)

            def text_exchange(request: bytes) -> str:
                """Expose a real server reply as text to exercise the writer wire-type refusal."""
                return exchange(request).decode("ascii")

            text_client = ProtectedWriteCurrentStatus(
                begun=begun,
                enrollment=enrollment,
                authority_key=authority_key,
                limits=prepared.policy.limits,
                reason_codes=POLICY.reason_codes,
                exchange=text_exchange,
                next_sequence=sequence.next,
                clock=lambda: CONTEXT.now + 1,
                response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
            )
            with pytest.raises(ValueError, match="immutable response bytes"):
                await asyncio.to_thread(text_client)

            expired = ProtectedWriteCurrentStatus(
                begun=begun,
                enrollment=enrollment,
                authority_key=authority_key,
                limits=prepared.policy.limits,
                reason_codes=POLICY.reason_codes,
                exchange=exchange,
                next_sequence=sequence.next,
                clock=lambda: CONTEXT.now + 21,
                response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
            )
            with pytest.raises(ValueError, match="enrollment is unavailable"):
                await asyncio.to_thread(expired)
            transition = ProtectedWriteTransitionHandler(
                hub=hub,
                policy=POLICY,
                enrollment_revision="enrollment",
                current_enrollments=control.current,
                writer_principal="EXAMPLE/writer",
                operator_principals=frozenset({operator.principal}),
                clock=lambda: CONTEXT.now + 1,
                sign_response=sign,
            )
            revoke_request = {
                key: value for key, value in source.items() if key not in {"auth", "signature"}
            }
            revoke_request.update(
                type="protected_write_revoke",
                sender=operator.principal,
                session_id=operator.session_id,
                request_id="current-revoke",
                timestamp=CONTEXT.now + 1,
                body={"reservation_id": begun.admission.reservation_id},
            )
            revoked_request = authenticate_protected_request(
                json.dumps(
                    sign_frame(
                        revoke_request,
                        key=operator_key,
                        nonce="current-revoke",
                        sequence=1,
                        timestamp=CONTEXT.now + 1,
                    )
                ),
                limits=prepared.policy.limits,
                enrollments=control.current(),
                authenticated_principal=operator.principal,
                replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                now=CONTEXT.now + 1,
            )
            assert json.loads(await transition(revoked_request))["body"]["disposition"] == "pending"
            with pytest.raises(ValueError, match="differs from enrolled begin"):
                await asyncio.to_thread(client)
            await control.revoke(enrollment.session_id)
            with pytest.raises(ConnectionClosed):
                await asyncio.to_thread(client)
    finally:
        sequence.close()
        replay_store.close()
        store.close()
