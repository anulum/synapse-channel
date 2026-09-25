# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable execution evidence regressions
from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from test_protected_write_proposal import LIMITS


@pytest.fixture
def evidence(tmp_path: Path) -> Iterator[ProtectedExecutionEvidenceStore]:
    journal = EventStore(tmp_path / "evidence.db")
    try:
        yield ProtectedExecutionEvidenceStore(journal, "text", LIMITS.operation_limits, 1024, 16)
    finally:
        journal.close()


def test_immutable_bytes_replay_and_read_after_reopen(
    evidence: ProtectedExecutionEvidenceStore, tmp_path: Path
) -> None:
    content = b"exact evidence\x00bytes"
    reference = evidence.write(content)
    assert evidence.read(reference, len(content) + 1) == content
    assert evidence.write(content) == reference
    assert len(evidence.journal.read_operations()) == 1
    evidence.journal.close()
    reopened = EventStore(tmp_path / "evidence.db")
    try:
        reader = replace(evidence, journal=reopened)
        assert reader.read(reference, 1024) == content
        assert reader.write(content) == reference
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["blob", "journal", "domain"])
def test_invalid_enrollment(evidence: ProtectedExecutionEvidenceStore, case: str) -> None:
    with pytest.raises(ValueError):
        replace(
            evidence,
            **{"max_blob_bytes": 0}
            if case == "blob"
            else {"max_journal_operations": True}
            if case == "journal"
            else {"domain": "unenrolled"},
        )


@pytest.mark.parametrize("content", [bytearray(b"x"), b"x" * 1025])
def test_invalid_blob_no_write(evidence: ProtectedExecutionEvidenceStore, content: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        evidence.write(cast(bytes, content))
    assert not evidence.journal.read_operations()


@pytest.mark.parametrize("case", ["budget", "overflow", "domain", "handle", "missing"])
def test_read_refuses_unbound_or_missing_reference(
    evidence: ProtectedExecutionEvidenceStore, case: str
) -> None:
    reference = dict(evidence.write(b"xx"))
    if case == "domain":
        reference["domain"] = "other"
        evidence = replace(
            evidence, limits=replace(evidence.limits, content_domains=frozenset({"text", "other"}))
        )
    elif case == "handle":
        reference["handle"] = "../escape"
    elif case == "missing":
        reference["sha256"] = reference["handle"] = "a" * 64
    with pytest.raises((ValueError, FileNotFoundError)):
        evidence.read(
            reference, cast(int, True) if case == "budget" else 1 if case == "overflow" else 1024
        )


@pytest.mark.parametrize(
    "case", ["conflict", "reference", "shape", "length", "bytes", "numeric_type"]
)
def test_real_retained_corruption_is_not_accepted(
    evidence: ProtectedExecutionEvidenceStore, case: str
) -> None:
    content = b"x"
    digest = hashlib.sha256(content).hexdigest()
    reference: dict[str, Any] = {
        "domain": "text",
        "handle": digest,
        "sha256": digest,
        "size_bytes": 1,
    }
    response: dict[str, Any] = {
        "reference": dict(reference),
        "content_base64": base64.b64encode(content).decode(),
    }
    if case == "reference":
        response["reference"]["handle"] = "changed"
    elif case == "shape":
        response["extra"] = 1
    elif case == "length":
        response["content_base64"] = ""
    elif case == "bytes":
        response["content_base64"] = base64.b64encode(b"y").decode()
    elif case == "numeric_type":
        response["reference"]["size_bytes"] = True
    evidence.journal.commit_operation(
        operation_key=f"protected-execution-evidence:text:{digest}",
        request_digest="a" * 64 if case == "conflict" else digest,
        response=response,
        events=(("test-corruption", {}),),
        intent={},
    )
    with pytest.raises(ValueError):
        evidence.read(reference, 1024)
    if case in ("conflict", "reference", "numeric_type"):
        with pytest.raises(ValueError, match="conflicts"):
            evidence.write(content)


def test_capacity_never_evicts_existing_evidence(evidence: ProtectedExecutionEvidenceStore) -> None:
    limited = replace(evidence, max_journal_operations=1)
    reference = limited.write(b"one")
    with pytest.raises(ValueError, match="budget"):
        limited.write(b"two")
    assert limited.read(reference, 1024) == b"one"
    assert limited.write(b"one") == reference
