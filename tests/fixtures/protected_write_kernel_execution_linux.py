# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual kernel refusal at the composed execution API
"""Refuse ambient process profiles before authority traffic or external effects."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from synapse_channel.core.message_auth import MessageAuthKey, MessageReplayCache
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_cgroup_parsing import parse_protected_cgroup_membership
from synapse_channel.core.protected_write_current_status import ProtectedWriteCurrentStatus
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_kernel_boundary import (
    ProtectedWriterKernelEvidence,
    ProtectedWriterKernelPolicy,
    capture_protected_writer_kernel_evidence,
)
from synapse_channel.core.protected_write_kernel_execution import (
    execute_kernel_checked_protected_write,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_sequence import ProtectedWriterSequence
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_unix_exchange import ProtectedWriteUnixExchange
from test_protected_write_admission import CONTEXT
from test_protected_write_preparation import _begun


def _unqualified_policy(evidence: ProtectedWriterKernelEvidence) -> ProtectedWriterKernelPolicy:
    """Declare a distinct proposed writer; no account or namespace is installed."""
    return ProtectedWriterKernelPolicy(
        writer_uid=os.geteuid() + 1,
        writer_gid=os.getegid(),
        shared_uid=os.geteuid(),
        host_mount_namespace=evidence.mount_namespace,
        writer_mount_namespace=(evidence.mount_namespace[0], evidence.mount_namespace[1] + 1),
        mountinfo_sha256=evidence.mountinfo_sha256,
        roots=evidence.roots,
        writable_mount_targets=frozenset(),
        writer_subtree_identity=(
            evidence.process_subtree.device,
            evidence.process_subtree.inode,
        ),
    )


@contextmanager
def _retained_client(
    root: int,
    begun: ProtectedWriteReservation,
    limits: ProtectedWriteProposalLimits,
) -> Iterator[tuple[ProtectedWriteCurrentStatus, socket.socket]]:
    """Construct an actual pinned socket client; refusals must never contact it."""
    path = Path(f"/proc/self/fd/{root}") / "endpoint" / "authority.sock"
    details = path.parent.stat()
    source = json.loads(begun.request_bytes)
    body = json.loads(begun.result_bytes)["body"]
    writer_key = MessageAuthKey("kernel-execution-writer", b"w" * 32, frozenset({source["sender"]}))
    authority_key = MessageAuthKey(
        "kernel-execution-authority", b"a" * 32, frozenset({source["target"]})
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
        writer_incarnation=body["writer_incarnation"],
    )
    sequence_root = os.fstat(root)
    with (
        ProtectedWriterSequence(
            root,
            root_identity=(sequence_root.st_dev, sequence_root.st_ino),
            name="execution.sequence",
            owner_uid=os.geteuid(),
            key_id=writer_key.key_id,
            sender=enrollment.principal,
            create=True,
        ) as sequence,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
    ):
        listener.bind(str(path))
        path.chmod(0o700)
        listener.listen(1)
        listener.setblocking(False)
        inode = path.stat()
        exchange = ProtectedWriteUnixExchange(
            path,
            parent_identity=(details.st_dev, details.st_ino),
            parent_uid=os.geteuid(),
            parent_gid=details.st_gid,
            parent_mode=details.st_mode & 0o777,
            socket_identity=(inode.st_dev, inode.st_ino),
            socket_uid=os.geteuid(),
            socket_gid=inode.st_gid,
            socket_mode=inode.st_mode & 0o777,
            peer_uid=os.geteuid(),
            max_wire_bytes=limits.json_limits.max_wire_bytes,
            timeout_seconds=1.0,
        )
        yield (
            ProtectedWriteCurrentStatus(
                begun=begun,
                enrollment=enrollment,
                authority_key=authority_key,
                limits=limits,
                reason_codes=frozenset(),
                exchange=exchange,
                next_sequence=sequence.next,
                clock=lambda: CONTEXT.now + 1,
                response_replay=MessageReplayCache(window_seconds=10, max_entries=32),
            ),
            listener,
        )


@pytest.mark.parametrize("failure", ["kernel", "scope", "roots", "identity"])
def test_composed_execution_refuses_before_consume_status_and_effect(
    tmp_path: Path, failure: Literal["kernel", "scope", "roots", "identity"]
) -> None:
    """Real ambient credentials or mismatched evidence cannot start an execution."""
    authority, prepared, state = _begun(tmp_path)
    with ExitStack() as stack:
        stack.callback(authority.close)
        service = stack.enter_context(EventStore(tmp_path / "execution.db"))
        root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        stack.callback(os.close, root)
        membership = parse_protected_cgroup_membership(Path("/proc/self/cgroup").read_bytes())
        subtree = os.open("/sys/fs/cgroup" + membership, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, subtree)
        roots = {"memory": root, "parent": root}
        observed = capture_protected_writer_kernel_evidence(roots, subtree_descriptor=subtree)
        policy = _unqualified_policy(observed)
        (tmp_path / "endpoint").mkdir(mode=0o700)
        begun = state.protected_write_reservations[prepared.admission.reservation_id]
        current, listener = stack.enter_context(
            _retained_client(root, begun, prepared.policy.limits)
        )
        limits = prepared.policy.limits.operation_limits
        if failure == "scope":
            limits = replace(limits, max_content_bytes=limits.max_content_bytes + 1)
        evidence = ProtectedExecutionEvidenceStore(service, "text", limits, 4096, 128)
        target = tmp_path / "records" / "note.md"
        assert not target.exists()
        records_before = tuple((tmp_path / "records").iterdir())
        sequence_before = (tmp_path / "execution.sequence").read_bytes()
        authority_before = authority.read_all()
        message = {
            "kernel": "kernel boundary differs",
            "scope": "evidence scope differs",
            "roots": "root scope differs",
            "identity": "root identity differs",
        }[failure]
        if failure == "roots":
            roots.pop("parent")
        elif failure == "identity":
            other = tmp_path / "substituted-root"
            other.mkdir(mode=0o700)
            substituted = os.open(other, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            stack.callback(os.close, substituted)
            roots["memory"] = substituted
        with pytest.raises(ValueError, match=message):
            execute_kernel_checked_protected_write(
                prepared,
                root_descriptors=roots,
                subtree_descriptor=subtree,
                kernel_policy=policy,
                current_status=current,
                execution_evidence=evidence,
                max_locks=8,
            )
        assert not target.exists()
        assert tuple((tmp_path / "records").iterdir()) == records_before
        assert (tmp_path / "execution.sequence").read_bytes() == sequence_before
        assert authority.read_all() == authority_before
        assert service.read_all() == []
        with pytest.raises(BlockingIOError):
            listener.accept()
