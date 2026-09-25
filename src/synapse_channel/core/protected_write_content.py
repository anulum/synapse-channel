# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Bind declared execution bytes to enrolled immutable content references."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from synapse_channel.core.protected_write_proposal import (
    ProtectedWriteProposalLimits,
    parse_protected_write_proposal,
)


@dataclass(frozen=True)
class ProtectedWriteContent:
    """Immutable execution inputs bound to a proposal, never execution permission."""

    proposal_sha256: str
    proposal_content: bytes
    operation_content: tuple[tuple[str, bytes], ...]


def bind_protected_write_content(
    proposal: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    artifacts: Mapping[tuple[str, str], bytes],
    domain_verifiers: Mapping[str, Callable[[bytes, str], bool]],
    max_artifact_bytes: int,
) -> ProtectedWriteContent:
    """Verify all content references and exact create/write resulting bytes.

    Parameters
    ----------
    proposal:
        Original complete proposal; strict representation validation precedes use.
    limits:
        Explicit enrolled proposal limits.
    artifacts:
        Trusted immutable in-memory snapshot keyed by exact domain and handle.
        No network/filesystem resolver or mutable byte buffers may be supplied.
    domain_verifiers:
        Enrolled local verifiers of artifact schema AND domain-specific digest.
        Each receives actual bytes and the expected reference digest and must
        return exactly True. Reference digests are not implicitly raw SHA-256.
    max_artifact_bytes:
        Positive explicit budget for unique referenced artifact bytes in total.

    Returns
    -------
    ProtectedWriteContent
        Immutable proposal content and ordered executable create/write payloads.

    Raises
    ------
    ValueError
        On missing/changed content, unverified domain, budget or output mismatch.

    Notes
    -----
    Actual resulting file hashes are independently checked as raw-byte SHA-256.
    This is not proof of filesystem topology, authorization, writer quiescence or
    completed execution. No artifact fetching, caching or persistent store is
    created; existing enrolled storage supplies the immutable snapshot.
    """
    if type(max_artifact_bytes) is not int or not 0 < max_artifact_bytes < 2**53:
        raise ValueError("invalid immutable artifact budget")
    parsed = parse_protected_write_proposal(proposal, limits=limits)
    source = json.loads(parsed.canonical_bytes)
    retained: dict[tuple[str, str], tuple[str, int, bytes]] = {}
    total = 0

    def resolve(reference: dict[str, object]) -> bytes:
        nonlocal total
        domain, handle = str(reference["domain"]), str(reference["handle"])
        digest, size = str(reference["sha256"]), reference["size_bytes"]
        key = (domain, handle)
        previous = retained.get(key)
        if previous is not None:
            if previous[:2] != (digest, size):
                raise ValueError("immutable handle has conflicting references")
            return previous[2]
        content = artifacts.get(key)
        if type(content) is not bytes or len(content) != size:
            raise ValueError("immutable artifact is absent or has changed size")
        total += len(content)
        if total > max_artifact_bytes:
            raise ValueError("immutable artifact budget exceeded")
        verifier = domain_verifiers.get(domain)
        if verifier is None or verifier(content, digest) is not True:
            raise ValueError("immutable artifact domain verification failed")
        retained[key] = (digest, len(content), content)
        return content

    proposal_content = resolve(source["content_reference"])
    execution: list[tuple[str, bytes]] = []
    for operation in source["auxiliary_operations"]:
        if operation["opcode"] not in ("create", "write"):
            continue
        content = resolve(operation["content_reference"])
        after = operation["paths"][0]["after"]
        if (
            len(content) != after["size_bytes"]
            or hashlib.sha256(content).hexdigest() != after["sha256"]
        ):
            raise ValueError("execution content does not match declared file bytes")
        execution.append((operation["operation_id"], content))
    return ProtectedWriteContent(parsed.proposal_sha256, proposal_content, tuple(execution))
