# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable execution evidence store
"""Bounded immutable execution evidence in the existing service EventStore.

This is evidence custody, never a second authority/grant ledger. The operator
must enroll the existing service database and sidecars before production use.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_operations import (
    ProtectedWriteOperationLimits,
    validate_protected_write_content_reference,
)


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


@dataclass(frozen=True)
class ProtectedExecutionEvidenceStore:
    """Explicit raw-SHA content domain backed by immutable keyed operations.

    Parameters
    ----------
    journal:
        Already enrolled service evidence journal, not the authority ledger.
    domain:
        Enrolled raw-byte-SHA evidence domain; this does not enroll new domains.
    limits:
        Existing enrolled content-reference vocabulary and representation budget.
    max_blob_bytes:
        Positive per-blob bound within the enrolled content budget.
    max_journal_operations:
        Positive total retained operation cap, shared with execution/step records.

    Notes
    -----
    No eviction, filesystem path supplied by references, separate database or
    custom signature protocol. FULL commit and exact replay reuse EventStore.
    Size limits bound legitimate writes, not externally corrupted database files;
    physical journal ownership and file/sidecar quotas remain mandatory.
    """

    journal: EventStore
    domain: str
    limits: ProtectedWriteOperationLimits
    max_blob_bytes: int
    max_journal_operations: int

    def __post_init__(self) -> None:
        """Reject unbounded or unrecognized evidence-store configuration."""
        if (
            type(self.max_blob_bytes) is not int
            or not 0 < self.max_blob_bytes <= self.limits.max_content_bytes
        ):
            raise ValueError("invalid evidence blob budget")
        if (
            type(self.max_journal_operations) is not int
            or not 0 < self.max_journal_operations < 2**53
        ):
            raise ValueError("invalid evidence journal budget")
        validate_protected_write_content_reference(
            {"domain": self.domain, "handle": "0" * 64, "sha256": "0" * 64, "size_bytes": 0},
            limits=self.limits,
        )

    def _key(self, digest: str) -> str:
        return f"protected-execution-evidence:{self.domain}:{digest}"

    def write(self, content: bytes) -> Mapping[str, object]:
        """Commit immutable bytes once and return their exact enrolled reference.

        Parameters
        ----------
        content:
            Immutable observation bytes within the explicit blob budget.

        Returns
        -------
        Mapping[str, object]
            Closed ContentRef with digest-derived handle, not a pathname.

        Raises
        ------
        ValueError
            On invalid content, exhausted journal budget or conflicting history.
        """
        if type(content) is not bytes or len(content) > self.max_blob_bytes:
            raise ValueError("invalid evidence bytes or blob budget")
        digest = hashlib.sha256(content).hexdigest()
        reference = {
            "domain": self.domain,
            "handle": digest,
            "sha256": digest,
            "size_bytes": len(content),
        }
        response = {
            "reference": reference,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        result = self.journal.commit_operation(
            operation_key=self._key(digest),
            request_digest=digest,
            response=response,
            events=(("protected_write_execution_evidence", reference),),
            intent={"family": "protected-execution-evidence"},
            max_retained_operations=self.max_journal_operations,
        )
        if result.outcome == "conflict" or _canonical(result.operation.response) != _canonical(
            response
        ):
            raise ValueError("immutable execution evidence conflicts with retained history")
        return reference

    def read(self, reference: Mapping[str, object], max_bytes: int) -> bytes:
        """Read an exact reference after reopen without resolving arbitrary paths.

        Parameters
        ----------
        reference:
            Closed reference in this exact raw-SHA domain.
        max_bytes:
            Positive caller read cap, including its overflow-probe byte if any.

        Returns
        -------
        bytes
            Complete immutable bytes; never a silently truncated prefix.

        Raises
        ------
        ValueError
            On wrong domain/handle, budgets, corrupted response or changed bytes.
        FileNotFoundError
            If the referenced immutable operation is not retained.
        """
        size = validate_protected_write_content_reference(dict(reference), limits=self.limits)
        if type(max_bytes) is not int or not 0 < max_bytes < 2**53:
            raise ValueError("invalid evidence read budget")
        if reference["domain"] != self.domain or reference["handle"] != reference["sha256"]:
            raise ValueError("evidence reference domain or digest handle mismatch")
        if size > min(max_bytes, self.max_blob_bytes):
            raise ValueError("evidence reference exceeds read budget")
        digest = str(reference["sha256"])
        operation = self.journal.get_operation(self._key(digest))
        if operation is None:
            raise FileNotFoundError("immutable execution evidence is absent")
        response = operation.response
        response_digest = hashlib.sha256(_canonical(response).encode("ascii")).hexdigest()
        if (
            operation.request_digest != digest
            or operation.response_sha256 != response_digest
            or set(response) != {"reference", "content_base64"}
            or response["reference"] != dict(reference)
        ):
            raise ValueError("immutable execution evidence record integrity mismatch")
        validate_protected_write_content_reference(response["reference"], limits=self.limits)
        encoded = response["content_base64"]
        if not isinstance(encoded, str) or len(encoded) != 4 * ((size + 2) // 3):
            raise ValueError("immutable evidence encoded length mismatch")
        content = base64.b64decode(encoded, validate=True)
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("immutable execution evidence bytes mismatch")
        return content
