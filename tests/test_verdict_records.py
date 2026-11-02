# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical evidence, verdict and correction bodies
"""Round-trip every record and refuse each malformed or oversized body."""

from __future__ import annotations

import json

import pytest

from synapse_channel.core.verdict_records import (
    MAX_MANIFEST_MEMBERS,
    MAX_REASON_BYTES,
    MAX_VERDICT_INPUTS,
    CorrectionRecord,
    EvidenceRecord,
    VerdictRecord,
    VerdictRecordError,
    correction_id,
    format_correction,
    format_evidence,
    format_verdict,
    parse_correction,
    parse_evidence,
    parse_verdict,
    validate_record_body,
)

R = "a" * 64
R2 = "b" * 64
A = "c" * 64


def _verdict(**changes: object) -> dict[str, object]:
    body: dict[str, object] = json.loads(
        format_verdict(
            VerdictRecord(
                namespace="PROJ",
                subject_sha256=A,
                outcome="accepted",
                inputs=(R,),
                acknowledged_corrections=(),
                author="PROJ/author",
                reviewer_sequence=1,
            )
        )
    )
    body.update(changes)
    return body


def _canonical(body: dict[str, object]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def test_each_record_round_trips_through_its_canonical_body() -> None:
    """Formatting then parsing returns the same record, and the body is canonical."""
    evidence = EvidenceRecord(sha256=R, namespace="PROJ", members=(R2,))
    verdict = VerdictRecord("PROJ", A, "accepted", (R,), (R2,), "PROJ/author", 3)
    correction = CorrectionRecord(
        namespace="PROJ", corrects=R, replacement=R2, reason="re-run with the fixed seed"
    )
    assert parse_evidence(format_evidence(evidence)) == evidence
    assert parse_verdict(format_verdict(verdict)) == verdict
    assert parse_correction(format_correction(correction)) == correction
    for kind, text in (
        ("evidence", format_evidence(evidence)),
        ("verdict", format_verdict(verdict)),
        ("correction", format_correction(correction)),
    ):
        validate_record_body(kind, text)


def test_correction_id_covers_the_pair_not_the_reason() -> None:
    """One acknowledgement covers the same correction whatever reason text it carries."""
    first = CorrectionRecord(namespace="PROJ", corrects=R, replacement=R2, reason="first wording")
    second = CorrectionRecord(namespace="PROJ", corrects=R, replacement=R2, reason="second wording")
    other = CorrectionRecord(namespace="PROJ", corrects=R2, replacement=R, reason="first wording")
    elsewhere = CorrectionRecord(namespace="OTHER", corrects=R, replacement=R2, reason="x")
    assert correction_id(first) == correction_id(second)
    assert correction_id(first) != correction_id(other)
    assert correction_id(first) != correction_id(elsewhere)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not JSON"),
        ("[]", "must be an object"),
        (_canonical({**_verdict(), "schema": "other"}), "schema must be"),
        (_canonical({**_verdict(), "extra": 1}), "fields must be exactly"),
        (json.dumps(_verdict(), indent=1), "not canonical"),
        (_canonical(_verdict(outcome="maybe")), "outcome must be"),
        (_canonical(_verdict(author="no spaces allowed")), "author must be"),
        (_canonical(_verdict(reviewer_sequence=0)), "reviewer_sequence"),
        (_canonical(_verdict(reviewer_sequence=True)), "reviewer_sequence"),
        (_canonical(_verdict(subject_sha256="A" * 64)), "lowercase hexadecimal"),
        (_canonical(_verdict(inputs="a")), "must be a list"),
        (_canonical(_verdict(inputs=[R2, R])), "sorted and unique"),
        (_canonical(_verdict(inputs=[A])), "own subject"),
        (
            _canonical(_verdict(inputs=[f"{i:064x}" for i in range(MAX_VERDICT_INPUTS + 1)])),
            "exceeds",
        ),
    ],
)
def test_malformed_verdict_bodies_are_refused(text: str, message: str) -> None:
    """Shape, bounds and canonical form are checked before a verdict is accepted."""
    with pytest.raises(VerdictRecordError, match=message):
        validate_record_body("verdict", text)


def test_evidence_and_correction_bounds_are_enforced() -> None:
    """Namespaces, manifest size, self-membership, reasons and replacements are bounded."""
    good = json.loads(format_evidence(EvidenceRecord(R, "PROJ")))
    with pytest.raises(VerdictRecordError, match="namespace is invalid"):
        parse_evidence(_canonical({**good, "namespace": "bad name"}))
    with pytest.raises(VerdictRecordError, match="itself"):
        parse_evidence(_canonical({**good, "members": [R]}))
    oversized = [f"{i:064x}" for i in range(MAX_MANIFEST_MEMBERS + 1)]
    with pytest.raises(VerdictRecordError, match="exceeds"):
        parse_evidence(_canonical({**good, "members": oversized}))
    correction = json.loads(format_correction(CorrectionRecord("PROJ", R, R2, "fixed")))
    with pytest.raises(VerdictRecordError, match="different replacement"):
        parse_correction(_canonical({**correction, "replacement": R}))
    with pytest.raises(VerdictRecordError, match="non-empty"):
        parse_correction(_canonical({**correction, "reason": "  "}))
    with pytest.raises(VerdictRecordError, match="exceeds"):
        parse_correction(_canonical({**correction, "reason": "x" * (MAX_REASON_BYTES + 1)}))
    with pytest.raises(VerdictRecordError, match="unknown record kind"):
        validate_record_body("note", "{}")
