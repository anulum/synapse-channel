# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real delayed-begin revocation race
"""Hold a writer subprocess while the durable authority revokes its plan."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

import pytest
from websockets.asyncio.server import unix_serve

from protected_write_authority_examples import (
    AUTHOR_KEY,
    AUTHORITY_KEY,
    RECOVERY_KEY,
    WRITER_KEY,
    _grant,
)
from protected_write_socket_examples import private_unix_directory as private_unix_directory
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim
from synapse_channel.core.message_auth import (
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_handler_binding import assemble_protected_write_dispatch
from synapse_channel.core.protected_write_preparation import ProtectedPreparationPolicy
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange
from test_protected_write_admission import _claim
from test_protected_write_effects import _plan
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request

_DELAYED_WRITER = r"""
import os
import sys
from pathlib import Path
from websockets.exceptions import ConnectionClosed
sys.path.insert(0, str(Path.cwd() / 'src'))
from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange
path = Path(sys.argv[1])
parent = path.parent.stat()
listener = path.lstat()
exchange = ProtectedWriteUnixExchange(
    path,
    parent_identity=(parent.st_dev, parent.st_ino),
    parent_uid=os.geteuid(),
    parent_gid=parent.st_gid,
    parent_mode=parent.st_mode & 0o777,
    socket_identity=(listener.st_dev, listener.st_ino),
    socket_uid=os.geteuid(),
    socket_gid=listener.st_gid,
    socket_mode=listener.st_mode & 0o777,
    peer_uid=os.geteuid(),
    max_wire_bytes=int(sys.argv[2]),
    timeout_seconds=2.0,
)
print('ready', flush=True)
request = sys.stdin.buffer.read()
try:
    exchange(request)
except ConnectionClosed:
    print('refused', flush=True)
else:
    print('accepted', flush=True)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["deliver", "cancel"])
async def test_durable_prebegin_revoke_refuses_delayed_process(
    tmp_path: Path, private_unix_directory: Path, delivery: Literal["deliver", "cancel"]
) -> None:
    """Refuse a delayed begin after durable revoke and reap a cancelled waiting process."""
    now = time.time()
    store = EventStore(tmp_path / "authority.db")
    replay = DurableMessageAuthReplayStore(
        tmp_path / "nonces.db", max_entries=32, window_seconds=10
    )
    try:
        records = tmp_path / "records"
        records.mkdir(mode=0o700)
        sentinel = records / "stable.txt"
        sentinel.write_bytes(b"preserved enrolled-parent sentinel\n")
        claim = _claim()
        claim.lease_expires_at = now + 60
        claim.worktree = str(tmp_path)
        record_claim(store, claim)
        policy = ProtectedAdmissionReplayPolicy(
            LIMITS, frozenset(), 10, verify_recovery=lambda _parent, _child: False
        )
        hub = SynapseHub(
            journal=store,
            protected_write_policies={"enrollment": policy},
            anti_rollback_checkpoint=False,
        )
        proposal = _plan()
        proposal["claims"][0]["lease_expires_at"] = claim.lease_expires_at
        digest = parse_protected_write_proposal(json.dumps(proposal), limits=LIMITS).proposal_sha256
        author = _grant(
            principal="EXAMPLE/author",
            session_id="author-session",
            key=AUTHOR_KEY,
            verbs=frozenset({"admit", "revoke"}),
            digest=digest,
            now=now,
        )
        writer = _grant(
            principal="EXAMPLE/writer",
            session_id="writer-session",
            key=WRITER_KEY,
            verbs=frozenset({"begin", "settle", "status"}),
            digest=digest,
            now=now,
            incarnation="incarnation",
        )
        recovery = _grant(
            principal="EXAMPLE/recovery",
            session_id="recovery-session",
            key=RECOVERY_KEY,
            verbs=frozenset({"recover"}),
            digest=digest,
            now=now,
        )
        grants = (author, writer, recovery)
        control = ProtectedEnrollmentControl(
            hub=hub,
            sealed_keys={grant.session_id: grant.key for grant in grants},
            clock=lambda: now,
        )
        for grant in grants:
            await control.issue(grant)
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
        replies = itertools.count(1)

        def sign(frame: dict[str, object]) -> dict[str, object]:
            number = next(replies)
            return sign_frame(
                frame,
                key=AUTHORITY_KEY,
                nonce=f"race-response-{number}",
                sequence=number,
                timestamp=now,
            )

        dispatch = assemble_protected_write_dispatch(
            hub=hub,
            effects=effects,
            replay_policy=policy,
            current_enrollments=control.current,
            writer=(writer.principal, "incarnation"),
            operator_principals=frozenset(),
            recovery_principals=frozenset({recovery.principal}),
            clock=lambda: now,
            sign_response=sign,
        )
        ingress = ProtectedWriteTransport(
            limits=effects.limits,
            current_enrollments=control.current,
            replay_store=replay,
            sequence_floor_mode=SequenceFloorMode.STRICT,
            clock=lambda: now,
            dispatch=dispatch,
            reason_codes=policy.reason_codes,
        )
        socket_path = private_unix_directory / "authority.sock"
        endpoint_metadata = private_unix_directory.stat()
        target = tmp_path / "records/note.md"

        def signed(
            verb: str,
            grant: ProtectedSessionEnrollment,
            *,
            sequence: int,
            body: dict[str, object],
        ) -> bytes:
            request = _request(verb)
            request.update(
                sender=grant.principal,
                session_id=grant.session_id,
                request_id=f"race-{verb}",
                proposal_sha256=digest,
                timestamp=now,
                body=body,
            )
            return json.dumps(
                sign_frame(
                    request,
                    key=grant.key,
                    nonce=f"race-{verb}",
                    sequence=sequence,
                    timestamp=now,
                )
            ).encode("ascii")

        async with unix_serve(
            ingress, path=socket_path, max_size=LIMITS.json_limits.max_wire_bytes
        ):
            socket_path.chmod(0o700)
            listener = socket_path.lstat()
            exchange = ProtectedWriteUnixExchange(
                socket_path,
                parent_identity=(endpoint_metadata.st_dev, endpoint_metadata.st_ino),
                parent_uid=os.geteuid(),
                parent_gid=endpoint_metadata.st_gid,
                parent_mode=endpoint_metadata.st_mode & 0o777,
                socket_identity=(listener.st_dev, listener.st_ino),
                socket_uid=os.geteuid(),
                socket_gid=listener.st_gid,
                socket_mode=listener.st_mode & 0o777,
                peer_uid=os.geteuid(),
                max_wire_bytes=LIMITS.json_limits.max_wire_bytes,
                timeout_seconds=2.0,
            )
            reply_replay = MessageReplayCache(window_seconds=10, max_entries=32)
            admitted = json.loads(
                await asyncio.to_thread(
                    exchange,
                    signed("admit", author, sequence=1, body={"proposal": proposal}),
                )
            )
            assert (
                verify_frame(
                    admitted,
                    keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                    replay_cache=reply_replay,
                    now=now,
                    required_sender="EXAMPLE/authority",
                )
                == VerificationResult.OK
            )
            assert admitted["body"]["disposition"] == "accepted"
            reservation_id = admitted["body"]["reservation_id"]
            delayed = signed(
                "begin",
                writer,
                sequence=1,
                body={"reservation_id": reservation_id, "writer_incarnation": "incarnation"},
            )
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _DELAYED_WRITER,
                str(socket_path),
                str(LIMITS.json_limits.max_wire_bytes),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert child.stdout is not None and child.stdin is not None
            try:
                assert await asyncio.wait_for(child.stdout.readline(), 5) == b"ready\n"
                assert not target.exists()
                before = (
                    (records.stat().st_dev, records.stat().st_ino),
                    tuple(sorted(os.listdir(records))),
                    hashlib.sha256(sentinel.read_bytes()).hexdigest(),
                )
                revoked = json.loads(
                    await asyncio.to_thread(
                        exchange,
                        signed(
                            "revoke",
                            author,
                            sequence=2,
                            body={"reservation_id": reservation_id},
                        ),
                    )
                )
                assert (
                    verify_frame(
                        revoked,
                        keys={AUTHORITY_KEY.key_id: AUTHORITY_KEY},
                        replay_cache=reply_replay,
                        now=now,
                        required_sender="EXAMPLE/authority",
                    )
                    == VerificationResult.OK
                )
                assert revoked["body"]["disposition"] == "effective"
                if delivery == "deliver":
                    child.stdin.write(delayed)
                    await child.stdin.drain()
                    child.stdin.close()
                    assert await asyncio.wait_for(child.stdout.readline(), 5) == b"refused\n"
                    assert await asyncio.wait_for(child.wait(), 5) == 0
                else:
                    assert child.returncode is None
                assert not target.exists()
                assert (
                    (records.stat().st_dev, records.stat().st_ino),
                    tuple(sorted(os.listdir(records))),
                    hashlib.sha256(sentinel.read_bytes()).hexdigest(),
                ) == before
                assert hub.state.protected_claim_custody == {}
                assert len(store.read_operations()) == 2
            finally:
                if child.returncode is None:
                    child.terminate()
                    await asyncio.wait_for(child.wait(), 5)
    finally:
        replay.close()
        store.close()
