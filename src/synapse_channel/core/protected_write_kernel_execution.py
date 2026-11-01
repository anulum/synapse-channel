# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — kernel-checked protected execution composition
"""Bind live Linux checks, signed authority status and durable effect evidence.

This is a trusted writer API, not a launcher or an independently qualified
installation. It does not create principals, namespaces, mounts or permissions.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from synapse_channel.core.persistence import OperationCommitResult
from synapse_channel.core.protected_write_current_status import ProtectedWriteCurrentStatus
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_kernel_boundary import (
    ProtectedWriterKernelPolicy,
    capture_protected_writer_kernel_evidence,
    verify_protected_writer_kernel_boundary,
)
from synapse_channel.core.protected_write_preparation import PreparedProtectedWrite


def execute_kernel_checked_protected_write(
    prepared: PreparedProtectedWrite,
    *,
    root_descriptors: Mapping[str, int],
    subtree_descriptor: int,
    kernel_policy: ProtectedWriterKernelPolicy,
    current_status: ProtectedWriteCurrentStatus,
    execution_evidence: ProtectedExecutionEvidenceStore,
    max_locks: int,
) -> tuple[OperationCommitResult, ...]:
    """Execute through live kernel evidence and signed current authority reads.

    Parameters
    ----------
    prepared:
        Fully prepared admission, retained content, effects and observations.
    root_descriptors:
        Supervisor-enrolled borrowed target directories. The mapping is copied;
        descriptor identity and exclusive custody must outlive this call.
    subtree_descriptor:
        Supervisor-retained actual cgroup directory bound to the writer policy.
    kernel_policy:
        Independently enrolled dedicated principal, namespace, exact mounts,
        retained roots and cgroup identity. Never derive an accepted policy
        from the writer's own untrusted declaration or current ambient profile.
    current_status:
        Signed authority status client bound to the exact retained begin and
        enrolled authority peer. Its transport and persistent sequence source
        must have been enrolled by the trusted launcher.
    execution_evidence:
        Existing private execution journal and immutable content store. Its
        operation vocabulary must match preparation; its own bounded journal
        and blob limits apply to all consumption and step/evidence records.
    max_locks:
        Positive upper bound on descriptors retained for declared file locks.

    Returns
    -------
    tuple[OperationCommitResult, ...]
        Exact one-time consumption and completed steps from the retained driver.
        Replay/conflict performs no filesystem effect and grants no retry.

    Raises
    ------
    ValueError
        On incompatible evidence/root scope or any failed current/kernel/effect check.
    OSError
        On unavailable native kernel, peer, journal or target descriptor I/O.
    Exception
        On transport, evidence or driver failure, preserving uncertain history.

    Notes
    -----
    Run only inside an independently qualified trusted writer launch. Fresh
    actual kernel capture is compared to the retained policy at every driver
    isolation boundary; no caller-supplied positive isolation callback is used.
    Signed current status is still a point-in-time read, not revocation fencing.
    Sealed installation, closed descriptors, exact syscall confinement, private
    proc access and exclusion of outside writable holders remain launch duties.
    Kernel facts alone cannot establish those duties. This call neither settles
    authority nor certifies quiescence, CLASS admission or whole-profile safety.
    Borrowed stores/descriptors remain caller owned on success and failure.
    """
    if execution_evidence.limits != prepared.policy.limits.operation_limits:
        raise ValueError("execution evidence scope differs from prepared operations")
    roots = MappingProxyType(dict(root_descriptors))
    if roots.keys() != prepared.policy.enrolled_roots.keys():
        raise ValueError("execution root scope differs from prepared operations")

    def verify_kernel() -> bool:
        """Compare fresh native process/root observations with the enrolled policy."""
        evidence = capture_protected_writer_kernel_evidence(
            roots, subtree_descriptor=subtree_descriptor
        )
        if {item.name: (item.device, item.inode) for item in evidence.roots} != dict(
            prepared.policy.enrolled_roots
        ):
            raise ValueError("execution root identity differs from prepared operations")
        return verify_protected_writer_kernel_boundary(evidence, kernel_policy)

    return execute_prepared_protected_write(
        prepared,
        root_descriptors=roots,
        owner_uid=kernel_policy.writer_uid,
        service_journal=execution_evidence.journal,
        max_journal_operations=execution_evidence.max_journal_operations,
        max_locks=max_locks,
        max_evidence_bytes=execution_evidence.max_blob_bytes,
        verify_current=current_status,
        verify_isolation=verify_kernel,
        store_evidence=execution_evidence.write,
        read_evidence=execution_evidence.read,
    )
