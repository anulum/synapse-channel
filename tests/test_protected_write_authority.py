# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real operator and protected ingress integration
"""Exercise one authority registry over real operator and request sockets."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
from websockets.exceptions import ConnectionClosed

from protected_write_socket_examples import private_unix_directory as private_unix_directory
from protected_write_socket_examples import requires_linux_peer_credentials
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_authority import assemble_protected_write_authority
from synapse_channel.core.protected_write_enrollment_journal import enrollment_issue_payload
from synapse_channel.core.protected_write_operator_socket import handle_operator_connection
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_unix_ingress import ProtectedWriteUnixIngress
from test_protected_write_admission import CONTEXT
from test_protected_write_admission_journal import POLICY
from test_protected_write_preparation import _begun


async def _operator(path: Path, command: dict[str, object]) -> bytes:
    """Send one framed command over the actual Unix socket and close the stream."""
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        raw = json.dumps(command).encode()
        writer.write(struct.pack("!I", len(raw)) + raw)
        await writer.drain()
        return await asyncio.wait_for(reader.readline(), 2)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
@requires_linux_peer_credentials
async def test_operator_issue_revoke_controls_same_signed_ingress(
    tmp_path: Path, private_unix_directory: Path
) -> None:
    """Actual operator commands affect the assembled ingress and survive replay.

    Current-UID component sockets exercise protocol wiring only. They do not
    satisfy the deployment listener's dedicated-UID/root-owned prerequisites.
    No physical recovery or effect permission is fabricated.
    """
    store, prepared, _state = _begun(tmp_path)
    try:
        replay_policy = replace(
            POLICY, limits=prepared.policy.limits, verify_recovery=lambda _parent, _child: False
        )
        hub = SynapseHub(
            journal=store,
            protected_write_policies={prepared.policy.enrollment_revision: replay_policy},
            anti_rollback_checkpoint=False,
        )
        begun = hub.state.protected_write_reservations[prepared.admission.reservation_id]
        source = json.loads(begun.request_bytes)
        key = MessageAuthKey("assembly-writer", b"w" * 32, frozenset({"EXAMPLE/writer"}))
        authority_key = MessageAuthKey(
            "assembly-authority", b"a" * 32, frozenset({"EXAMPLE/authority"})
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
            verbs=frozenset({"begin", "settle", "status"}),
            expires_at=CONTEXT.now + 20,
            key=key,
            writer_incarnation="incarnation",
        )
        replies = itertools.count(1)

        def sign(frame: dict[str, object]) -> dict[str, Any]:
            """Sign a real authority response with a fresh deterministic nonce and sequence."""
            number = next(replies)
            return sign_frame(
                frame,
                key=authority_key,
                nonce=f"assembly-reply-{number}",
                sequence=number,
                timestamp=CONTEXT.now + 1,
            )

        with DurableMessageAuthReplayStore(
            tmp_path / "assembly-nonces.db", max_entries=32, window_seconds=10
        ) as replay:
            authority = assemble_protected_write_authority(
                hub=hub,
                sealed_keys={enrollment.session_id: key},
                effects=prepared.policy,
                replay_policy=replay_policy,
                writer=(enrollment.principal, "incarnation"),
                operator_principals=frozenset({"EXAMPLE/operator"}),
                recovery_principals=frozenset({"EXAMPLE/recovery"}),
                clock=lambda: CONTEXT.now + 1,
                sign_response=sign,
                replay_store=replay,
                sequence_floor_mode=SequenceFloorMode.STRICT,
            )
            assert authority.operator_control.current() == {}
            before_events = store.read_all()
            before_operations = store.read_operations()
            custody = dict(hub.state.protected_claim_custody)
            operator_path = private_unix_directory / "operator.sock"
            ingress_path = private_unix_directory / "ingress.sock"
            operator_server = await asyncio.start_unix_server(
                lambda reader, writer: handle_operator_connection(
                    reader,
                    writer,
                    control=authority.operator_control,
                    operator_uid=os.geteuid(),
                ),
                path=operator_path,
            )
            peer_ingress = ProtectedWriteUnixIngress(
                authority.ingress, allowed_uids=frozenset({os.geteuid()})
            )
            async with (
                operator_server,
                unix_serve(
                    peer_ingress,
                    path=ingress_path,
                    max_size=prepared.policy.limits.json_limits.max_wire_bytes,
                ),
            ):
                metadata = {
                    name: value
                    for name, value in enrollment_issue_payload(enrollment).items()
                    if name not in {"schema_version", "key_fingerprint"}
                }
                assert await _operator(operator_path, {"op": "issue", "metadata": metadata}) == (
                    b'{"ok":true}\n'
                )
                assert authority.operator_control.current() == {enrollment.session_id: enrollment}
                request = {
                    name: value
                    for name, value in source.items()
                    if name not in {"auth", "signature"}
                }
                request.update(
                    type="protected_write_status",
                    request_id="assembly-status",
                    timestamp=CONTEXT.now + 1,
                    body={"reservation_id": begun.admission.reservation_id},
                )
                async with unix_connect(ingress_path) as client:
                    await client.send(
                        json.dumps(
                            sign_frame(
                                request,
                                key=key,
                                nonce="assembly-request",
                                sequence=1,
                                timestamp=CONTEXT.now + 1,
                            )
                        )
                    )
                    response = json.loads(await asyncio.wait_for(client.recv(), 2))
                    assert (
                        verify_frame(
                            response,
                            keys={authority_key.key_id: authority_key},
                            replay_cache=MessageReplayCache(window_seconds=10, max_entries=32),
                            now=CONTEXT.now + 1,
                            required_sender=enrollment.target,
                        )
                        == VerificationResult.OK
                    )
                    assert response["body"]["disposition"] == "known"
                    assert (
                        await _operator(
                            operator_path, {"op": "revoke", "session_id": enrollment.session_id}
                        )
                        == b'{"ok":true}\n'
                    )
                    await client.send(
                        json.dumps(
                            sign_frame(
                                request,
                                key=key,
                                nonce="assembly-after-revoke",
                                sequence=2,
                                timestamp=CONTEXT.now + 1,
                            )
                        )
                    )
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(client.recv(), 2)
            await peer_ingress.aclose()
            assert authority.operator_control.current() == {}
            assert len(store.read_all()) == len(before_events) + 2
            assert store.read_operations() == before_operations
            assert hub.state.protected_write_reservations[begun.admission.reservation_id] == begun
            assert hub.state.protected_claim_custody == custody
            restored = SynapseHub(
                journal=store,
                protected_write_policies={prepared.policy.enrollment_revision: replay_policy},
                anti_rollback_checkpoint=False,
            )
            assert restored.state.protected_enrollments[enrollment.session_id].revoked
            assert not (tmp_path / "records/note.md").exists()
    finally:
        store.close()
