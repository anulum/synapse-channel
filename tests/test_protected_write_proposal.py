# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — full protected-write proposal representation regressions
from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from synapse_channel.core.protected_write_json import ProtectedWriteJsonLimits
from synapse_channel.core.protected_write_operations import ProtectedWriteOperationLimits
from synapse_channel.core.protected_write_proposal import (
    ProtectedWriteProposalError,
    ProtectedWriteProposalLimits,
    parse_protected_write_proposal,
)

LIMITS = ProtectedWriteProposalLimits(
    ProtectedWriteJsonLimits(20000, 20, 1024, 1000),
    ProtectedWriteOperationLimits(
        128, 100, 4096, frozenset({"memory"}), frozenset({"text"}), frozenset({"0600", "0700"})
    ),
    4,
    1000,
)


def _proposal() -> dict[str, object]:
    content = b"Reference: retained source event.\n"
    digest = hashlib.sha256(content).hexdigest()
    state = {"kind": "file", "sha256": digest, "size_bytes": len(content), "mode": "0600"}
    absent = {"kind": "absent"}
    reference = {
        "domain": "text",
        "handle": "source-content",
        "sha256": digest,
        "size_bytes": len(content),
    }
    return {
        "target_project": "EXAMPLE",
        "recovery_policy_revision": "recovery-1",
        "content_reference": reference,
        "claims": [
            {
                "task_id": "task-1",
                "owner": "EXAMPLE/author",
                "epoch": 1,
                "version": 2,
                "lease_expires_at": 1788649200.0,
            }
        ],
        "operations": [
            {
                "operation_id": "record",
                "root_id": "memory",
                "relative_path": "records/note.md",
                "action": "create",
                "claim_task_id": "task-1",
                "before": absent,
                "after_sha256": digest,
                "after_size_bytes": len(content),
                "mode": "0600",
            }
        ],
        "auxiliary_operations": [
            {
                "operation_id": "create-record",
                "opcode": "create",
                "paths": [
                    {
                        "root_id": "memory",
                        "relative_path": "records/note.md",
                        "before": absent,
                        "after": state,
                    }
                ],
                "content_reference": reference,
            }
        ],
    }


def test_full_proposal_binds_canonical_bytes_and_ordered_claims() -> None:
    document = _proposal()
    parsed = parse_protected_write_proposal(json.dumps(document), limits=LIMITS)
    assert json.loads(parsed.canonical_bytes) == document
    assert parsed.claim_ids == ("task-1",)
    assert parsed.executable_ids == ("create-record",)
    assert len(parsed.canonical_bytes) == 1071
    assert parsed.proposal_sha256 == (
        "0438be3ba38f69da3624a6fbc0e21956f848bece6939ad870aa08e52c551136b"
    )
    preimage = (
        b'{"domain":"synapse-protected-write.v1/proposal","proposal":'
        + parsed.canonical_bytes
        + b"}"
    )
    assert parsed.proposal_sha256 == hashlib.sha256(preimage).hexdigest()
    assert parsed.proposal_sha256 != hashlib.sha256(parsed.canonical_bytes).hexdigest()
    assert parse_protected_write_proposal(parsed.canonical_bytes, limits=LIMITS) == parsed
    assert parse_protected_write_proposal(json.dumps(document, indent=4), limits=LIMITS) == parsed
    for field in ("canonical_bytes", "proposal_sha256", "claim_ids", "executable_ids"):
        with pytest.raises(FrozenInstanceError):
            setattr(parsed, field, "changed")
    document["target_project"] = "changed"
    assert json.loads(parsed.canonical_bytes)["target_project"] == "EXAMPLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", True),
        ("version", 2.0),
        ("version", 1001),
        ("lease_expires_at", 1788649200),
        ("lease_expires_at", "1788649200.0"),
        ("owner", ""),
        ("task_id", "not an id"),
        ("extra", "forbidden"),
    ],
)
def test_claim_witness_types_and_fields_are_exact(field: str, value: object) -> None:
    document = _proposal()
    cast(list[dict[str, object]], document["claims"])[0][field] = value
    with pytest.raises(ProtectedWriteProposalError):
        parse_protected_write_proposal(json.dumps(document), limits=LIMITS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("domain", "unknown"),
        ("handle", ""),
        ("sha256", "A" * 64),
        ("size_bytes", True),
        ("size_bytes", 5000),
        ("extra", None),
    ],
)
def test_content_reference_is_closed_and_domain_bound(field: str, value: object) -> None:
    document = _proposal()
    cast(dict[str, object], document["content_reference"])[field] = value
    with pytest.raises(ProtectedWriteProposalError):
        parse_protected_write_proposal(json.dumps(document), limits=LIMITS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("claims", []),
        ("claims", None),
        ("claims", [None]),
        ("operations", None),
        ("operations", [None]),
        ("auxiliary_operations", None),
        ("auxiliary_operations", [None]),
        ("extra", True),
        ("target_project", "bad name"),
    ],
)
def test_malformed_full_proposal_refuses(field: str, value: object) -> None:
    document = _proposal()
    document[field] = value
    with pytest.raises(ProtectedWriteProposalError):
        parse_protected_write_proposal(json.dumps(document), limits=LIMITS)


def test_duplicate_and_over_budget_claims_refuse() -> None:
    document = _proposal()
    claims = cast(list[dict[str, object]], document["claims"])
    claims.append(dict(claims[0]))
    with pytest.raises(ProtectedWriteProposalError, match="duplicate"):
        parse_protected_write_proposal(json.dumps(document), limits=LIMITS)
    with pytest.raises(ProtectedWriteProposalError, match="array"):
        parse_protected_write_proposal(json.dumps(document), limits=replace(LIMITS, max_claims=1))


def test_operation_must_reference_a_declared_claim() -> None:
    document = _proposal()
    cast(list[dict[str, object]], document["operations"])[0]["claim_task_id"] = "foreign"
    with pytest.raises(ProtectedWriteProposalError, match="witness"):
        parse_protected_write_proposal(json.dumps(document), limits=LIMITS)


@pytest.mark.parametrize("value", [0, -1, True, 1.0, 1 << 53])
def test_counter_and_claim_budgets_are_explicit(value: object) -> None:
    with pytest.raises(ProtectedWriteProposalError, match="budget"):
        replace(LIMITS, max_counter=cast(int, value))
    with pytest.raises(ProtectedWriteProposalError, match="budget"):
        replace(LIMITS, max_claims=cast(int, value))


def test_changed_claim_expiry_changes_proposal_digest() -> None:
    document = _proposal()
    prior = parse_protected_write_proposal(json.dumps(document), limits=LIMITS)
    cast(list[dict[str, object]], document["claims"])[0]["lease_expires_at"] = 1788649201.0
    changed = parse_protected_write_proposal(json.dumps(document), limits=LIMITS)
    assert changed.proposal_sha256 != prior.proposal_sha256
