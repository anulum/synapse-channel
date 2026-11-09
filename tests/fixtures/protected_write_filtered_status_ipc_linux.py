# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — filtered writer signed authority IPC
"""Verify actual protected status IPC under the writer's native syscall restriction."""

from __future__ import annotations

import asyncio
import itertools
import json
import multiprocessing
import os
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Literal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.server import unix_serve
from websockets.exceptions import ConnectionClosed

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    MessageAuthKey,
    MessageReplayCache,
    sign_event_frame,
    sign_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_current_status import ProtectedWriteCurrentStatus
from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_status import ProtectedWriteStatus
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICIES, POLICY
from test_protected_write_preparation import _begun


def _filtered_writer(
    path: Path,
    parent_identity: tuple[int, int],
    socket_identity: tuple[int, int],
    begun: ProtectedWriteReservation,
    enrollment: ProtectedSessionEnrollment,
    authority_key: MessageAuthKey | EventSignatureKey,
    limits: ProtectedWriteProposalLimits,
    channel: Connection,
) -> None:
    """Report both signed status observations under the native descriptor filter."""
    root = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        sequence = ProtectedWriterSequence(
            root,
            root_identity=parent_identity,
            name="filtered.sequence",
            owner_uid=os.geteuid(),
            key_id=enrollment.key.key_id,
            sender=enrollment.principal,
        )
    finally:
        os.close(root)
    try:
        parent_metadata = path.parent.stat()
        listener_metadata = path.lstat()
        exchange = ProtectedWriteUnixExchange(
            path,
            parent_identity=parent_identity,
            parent_uid=os.geteuid(),
            parent_gid=parent_metadata.st_gid,
            parent_mode=parent_metadata.st_mode & 0o777,
            socket_identity=socket_identity,
            socket_uid=os.geteuid(),
            socket_gid=listener_metadata.st_gid,
            socket_mode=listener_metadata.st_mode & 0o777,
            peer_uid=os.geteuid(),
            max_wire_bytes=limits.json_limits.max_wire_bytes,
            timeout_seconds=2.0,
        )
        client = ProtectedWriteCurrentStatus(
            begun=begun,
            enrollment=enrollment,
            authority_key=authority_key,
            limits=limits,
            reason_codes=POLICY.reason_codes,
            exchange=exchange,
            next_sequence=sequence.next,
            clock=lambda: CONTEXT.now + 1,
            response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
        )
        install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
        reservation = client()
        channel.send(reservation.transition_sequence)
        assert channel.poll(5) and channel.recv() == "revoked"
        try:
            current = client()
        except ConnectionClosed:
            channel.send("refused")
        else:
            channel.send(current.transition_sequence)
    finally:
        sequence.close()
        channel.close()


async def _receive(channel: Connection) -> object:
    """Receive one actual process result within a bounded readiness wait."""
    assert await asyncio.to_thread(channel.poll, 5)
    return channel.recv()


async def _exercise_filtered_status(
    tmp_path: Path,
    private_unix_directory: Path,
    state: Literal["active", "revoked", "cancelled", "unbound"],
    response_profile: Literal["hmac", "ed25519"],
) -> None:
    """Own the actual hub, filtered process and cleanup for each authority state."""
    store, prepared, _state = _begun(tmp_path)
    replay_store = DurableMessageAuthReplayStore(
        tmp_path / "filtered-replay.db", max_entries=32, window_seconds=10
    )
    root = os.open(private_unix_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    details = os.fstat(root)
    sequence = ProtectedWriterSequence(
        root,
        root_identity=(details.st_dev, details.st_ino),
        name="filtered.sequence",
        owner_uid=os.geteuid(),
        key_id="filtered-writer",
        sender="EXAMPLE/writer",
        create=True,
    )
    os.close(root)
    parent_channel, child_channel = multiprocessing.get_context("spawn").Pipe()
    worker: BaseProcess | None = None
    try:
        hub = SynapseHub(
            journal=store, protected_write_policies=POLICIES, anti_rollback_checkpoint=False
        )
        begun = hub.state.protected_write_reservations[prepared.admission.reservation_id]
        source = json.loads(begun.request_bytes)
        writer_key = MessageAuthKey("filtered-writer", b"w" * 32, frozenset({"EXAMPLE/writer"}))
        shared_authority_key = MessageAuthKey(
            "filtered-authority", b"a" * 32, frozenset({"EXAMPLE/authority"})
        )
        authority_private = Ed25519PrivateKey.generate()
        authority_key: MessageAuthKey | EventSignatureKey = (
            EventSignatureKey.from_private_key(
                key_id="filtered-authority",
                private_key=authority_private,
                senders=frozenset({"EXAMPLE/authority"}),
            )
            if response_profile == "ed25519"
            else shared_authority_key
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
        control = ProtectedEnrollmentControl(
            hub=hub, sealed_keys={enrollment.session_id: writer_key}, clock=lambda: CONTEXT.now + 1
        )
        await control.issue(enrollment)
        replies = itertools.count(1)

        def sign(frame: dict[str, object]) -> dict[str, object]:
            """Sign actual authority responses with distinct replay sequences."""
            number = next(replies)
            if response_profile == "ed25519":
                return sign_event_frame(
                    frame,
                    key_id=authority_key.key_id,
                    private_key=authority_private,
                    nonce=f"filtered-reply-{number}",
                    sequence=number,
                    signed_at=CONTEXT.now + 1,
                )
            return sign_frame(
                frame,
                key=shared_authority_key,
                nonce=f"filtered-reply-{number}",
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
        path = private_unix_directory / "filtered-authority.sock"
        if state == "unbound":
            path.mkdir(mode=0o700)
        async with unix_serve(
            ingress, path=path, max_size=prepared.policy.limits.json_limits.max_wire_bytes
        ):
            path.chmod(0o700)
            listener = path.lstat()
            worker = multiprocessing.get_context("spawn").Process(
                target=_filtered_writer,
                args=(
                    path,
                    (details.st_dev, details.st_ino),
                    (listener.st_dev, listener.st_ino),
                    begun,
                    enrollment,
                    authority_key,
                    prepared.policy.limits,
                    child_channel,
                ),
            )
            worker.start()
            child_channel.close()
            assert await _receive(parent_channel) == begun.transition_sequence
            custody = dict(hub.state.protected_claim_custody)
            if state == "cancelled":
                assert worker.is_alive()
                assert sequence.next() == 2
            else:
                if state == "revoked":
                    await control.revoke(enrollment.session_id)
                parent_channel.send("revoked")
                expected = "refused" if state == "revoked" else begun.transition_sequence
                assert await _receive(parent_channel) == expected
                await asyncio.to_thread(worker.join, 5)
                assert worker.exitcode == 0
                assert sequence.next() == 3
            assert hub.state.protected_write_reservations[begun.admission.reservation_id] == begun
            assert hub.state.protected_claim_custody == custody
            assert not (tmp_path / "records/note.md").exists()
    finally:
        if worker is not None:
            if worker.is_alive():
                worker.kill()
            await asyncio.to_thread(worker.join, 5)
            worker.close()
        parent_channel.close()
        child_channel.close()
        sequence.close()
        replay_store.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "revoked", "cancelled", "unbound"])
@pytest.mark.parametrize("response_profile", ["hmac", "ed25519"])
async def test_filtered_writer_status_and_process_lifecycle(
    tmp_path: Path,
    private_unix_directory: Path,
    state: Literal["active", "revoked", "cancelled", "unbound"],
    response_profile: Literal["hmac", "ed25519"],
) -> None:
    """Bind status to the actor, and reap a waiting child or a refused launch.

    A parent pipe notification cannot revoke enrollment: the active control
    still returns signed status, while a durable actor revoke refuses it.
    Cancellation owns the still-waiting child; a real socket-path collision
    refuses before spawning. Spawn carries trusted fixture inputs only and
    does not certify a dedicated-UID closed-descriptor production launch.
    """
    if state == "unbound":
        with pytest.raises(OSError):
            await _exercise_filtered_status(
                tmp_path, private_unix_directory, state, response_profile
            )
        assert (private_unix_directory / "filtered-authority.sock").is_dir()
    else:
        await _exercise_filtered_status(tmp_path, private_unix_directory, state, response_profile)
