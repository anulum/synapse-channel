# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Enforce exact enrolled effect permissions and explicit durability ordering."""

from __future__ import annotations

import json
from collections.abc import Mapping

from synapse_channel.core.protected_write_proposal import (
    ProtectedWriteProposalLimits,
    parse_protected_write_proposal,
)

PathKey = tuple[str, str]


def verify_protected_effects(
    proposal: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    primary_claims: Mapping[PathKey, frozenset[str]],
    auxiliary_opcodes: Mapping[PathKey, frozenset[str]],
    enrolled_parents: Mapping[PathKey, PathKey],
) -> str:
    """Verify explicit effect authority and file/parent fsync obligations.

    Parameters
    ----------
    proposal:
        Original complete success-only proposal, strictly validated on entry.
    limits:
        Explicit representation limits.
    primary_claims:
        Operator-enrolled exact target paths and separately authorized claim IDs.
        No prefix inheritance, wildcard expansion or implicit whole-root authority.
    auxiliary_opcodes:
        Operator-enrolled exact paths and permitted executable opcodes.
    enrolled_parents:
        Actual canonical immediate-parent bindings established by the trusted
        descriptor enrollment layer, not client-selected textual parent guesses.
        Root-level targets may bind a parent exposed through another enrolled root.

    Returns
    -------
    str
        Validated proposal digest, never an execution capability.

    Raises
    ------
    ValueError
        On absent permission, parent enrollment or missing/out-of-order fsync.

    Notes
    -----
    This checks the declared successful execution path. Failure still stops all
    mutation and retains recovery custody; it never triggers cleanup. Current
    claims, physical topology, bytes, signer/session/ACL and execution-time
    descriptor checks remain independent required gates. Inputs must be immutable
    trusted enrollment snapshots, not maps supplied by the requesting author.
    """
    parsed = parse_protected_write_proposal(proposal, limits=limits)
    source = json.loads(parsed.canonical_bytes)
    for operation in source["operations"]:
        key = (operation["root_id"], operation["relative_path"])
        allowed = primary_claims.get(key)
        if not isinstance(allowed, frozenset) or operation["claim_task_id"] not in allowed:
            raise ValueError("primary effect lacks exact enrolled claim permission")
    dirty_files: set[PathKey] = set()
    dirty_parents: set[PathKey] = set()
    for operation in source["auxiliary_operations"]:
        opcode = operation["opcode"]
        keys = [(path["root_id"], path["relative_path"]) for path in operation["paths"]]
        for key in keys:
            allowed_ops = auxiliary_opcodes.get(key)
            if not isinstance(allowed_ops, frozenset) or opcode not in allowed_ops:
                raise ValueError("auxiliary effect lacks exact enrolled opcode permission")
        if opcode in ("mkdir", "create", "rename", "unlink"):
            for key in keys:
                parent = enrolled_parents.get(key)
                if parent is None:
                    raise ValueError("entry mutation lacks enrolled immediate parent")
                dirty_parents.add(parent)
        if opcode in ("create", "write"):
            dirty_files.add(keys[0])
        elif opcode == "rename":
            if keys[0] in dirty_files:
                raise ValueError("rename source must be fsynced before publication")
            dirty_files.discard(keys[1])
        elif opcode == "unlink":
            dirty_files.discard(keys[0])
        elif opcode == "fsync":
            if operation["paths"][0]["before"]["kind"] == "directory":
                dirty_parents.discard(keys[0])
            else:
                dirty_files.discard(keys[0])
    if dirty_files or dirty_parents:
        raise ValueError("declared effects leave unsynced file or parent mutations")
    return parsed.proposal_sha256
