# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write proposal decoding and digest binding
"""Bind a fully described proposal to the protected-write proposal hash domain.

Parsing never authenticates the author, refreshes a claim, resolves a content
handle or grants access to a filesystem. Those checks belong to live admission.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.protected_write_json import (
    ProtectedWriteJsonLimits,
    decode_protected_write_json,
)
from synapse_channel.core.protected_write_operations import (
    ProtectedWriteOperationLimits,
    ProtectedWriteOperationsError,
    validate_protected_write_content_reference,
    validate_protected_write_operations,
)


class ProtectedWriteProposalError(SynapseError, ValueError):
    """A proposal lacks exact claim, content or declared operation bindings.

    Attributes
    ----------
    code : str
        Stable classification returned by ``error_code``.
    """

    code = "protected_write_proposal"


@dataclass(frozen=True)
class ProtectedWriteProposalLimits:
    """Explicit parser budgets; not an enrollment or authority credential.

    Parameters
    ----------
    json_limits:
        Raw JSON representation budgets.
    operation_limits:
        Operation budgets and already enrolled vocabulary.
    max_claims:
        Positive maximum number of claim witnesses in a proposal.
    max_counter:
        Positive maximum claim epoch/version value, at most 2**53-1.
    """

    json_limits: ProtectedWriteJsonLimits
    operation_limits: ProtectedWriteOperationLimits
    max_claims: int
    max_counter: int

    def __post_init__(self) -> None:
        """Refuse absent, coerced or unsupported witness budgets."""
        for value in (self.max_claims, self.max_counter):
            if type(value) is not int or not 0 < value <= (1 << 53) - 1:
                raise ProtectedWriteProposalError("invalid claim witness budget")


@dataclass(frozen=True)
class ParsedProtectedWriteProposal:
    """Immutable representation evidence, never an execution capability.

    Parameters
    ----------
    canonical_bytes:
        Canonical ASCII proposal bytes without a newline or domain wrapper.
    proposal_sha256:
        SHA-256 of the domain-wrapped canonical proposal, not a file hash.
    claim_ids:
        Claim identifiers in declared order.
    executable_ids:
        Executable operation identifiers in declared order for settlement checks.
    """

    canonical_bytes: bytes
    proposal_sha256: str
    claim_ids: tuple[str, ...]
    executable_ids: tuple[str, ...]


def parse_protected_write_proposal(
    raw: str | bytes, *, limits: ProtectedWriteProposalLimits
) -> ParsedProtectedWriteProposal:
    """Decode and bind one complete proposal without reading its content store.

    Parameters
    ----------
    raw:
        Strict raw JSON proposal, not an envelope or pre-decoded dictionary.
    limits:
        Explicit representation and claim budgets.

    Returns
    -------
    ParsedProtectedWriteProposal
        Immutable canonical bytes, proposal-domain digest and ordered bindings.

    Raises
    ------
    ProtectedWriteProposalError
        For malformed claim/reference fields or unbound operation claims.
    ValueError
        For raw JSON or executable operation representation violations.

    Notes
    -----
    Lease freshness, actual ownership, epoch/version equality, content integrity
    and enrolled filesystem topology still require fresh authoritative checks.
    """
    return _bind_protected_write_proposal(
        decode_protected_write_json(raw, limits=limits.json_limits), limits=limits
    )


def _bind_protected_write_proposal(
    value: object, *, limits: ProtectedWriteProposalLimits
) -> ParsedProtectedWriteProposal:
    """Bind a subtree of an already raw-validated document without re-budgeting."""
    proposal = _fields(
        value,
        {
            "target_project",
            "claims",
            "operations",
            "auxiliary_operations",
            "content_reference",
            "recovery_policy_revision",
        },
    )
    _identifier(proposal["target_project"], limits)
    _identifier(proposal["recovery_policy_revision"], limits)
    claims = proposal["claims"]
    if not isinstance(claims, list) or not 0 < len(claims) <= limits.max_claims:
        raise ProtectedWriteProposalError("invalid claim witness array")
    claim_ids: list[str] = []
    for claim in claims:
        witness = _fields(claim, {"task_id", "owner", "epoch", "version", "lease_expires_at"})
        task_id = _identifier(witness["task_id"], limits)
        _identifier(witness["owner"], limits)
        if task_id in claim_ids:
            raise ProtectedWriteProposalError("duplicate claim witness")
        for field in ("epoch", "version"):
            value = witness[field]
            if type(value) is not int or not 0 <= value <= limits.max_counter:
                raise ProtectedWriteProposalError("invalid claim counter")
        if type(witness["lease_expires_at"]) is not float:
            raise ProtectedWriteProposalError("lease timestamp must be binary64")
        claim_ids.append(task_id)
    try:
        validate_protected_write_content_reference(
            proposal["content_reference"], limits=limits.operation_limits
        )
    except ProtectedWriteOperationsError as exc:
        raise ProtectedWriteProposalError("invalid proposal content reference") from exc
    operations, auxiliary = proposal["operations"], proposal["auxiliary_operations"]
    if not isinstance(operations, list) or not isinstance(auxiliary, list):
        raise ProtectedWriteProposalError("invalid operation arrays")
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("claim_task_id") not in claim_ids:
            raise ProtectedWriteProposalError("operation has no declared claim witness")
    executable_ids = tuple(_operation_id(operation, limits) for operation in auxiliary)
    validate_protected_write_operations(operations, auxiliary, limits=limits.operation_limits)
    canonical = _canonical(proposal)
    preimage = _canonical({"domain": "synapse-protected-write.v1/proposal", "proposal": proposal})
    return ParsedProtectedWriteProposal(
        canonical, hashlib.sha256(preimage).hexdigest(), tuple(claim_ids), executable_ids
    )


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _fields(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProtectedWriteProposalError("incorrect proposal object fields")
    return dict(value)


def _identifier(value: object, limits: ProtectedWriteProposalLimits) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limits.operation_limits.max_identifier_chars
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is None
    ):
        raise ProtectedWriteProposalError("invalid proposal identifier")
    return value


def _operation_id(value: object, limits: ProtectedWriteProposalLimits) -> str:
    if not isinstance(value, dict):
        raise ProtectedWriteProposalError("invalid executable operation")
    return _identifier(value.get("operation_id"), limits)
