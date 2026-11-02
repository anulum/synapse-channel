# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — canonical evidence, verdict and correction progress-note bodies
"""Encode and strictly parse the three records that make verdicts correctable.

A reviewer's verdict on an object is only as good as the inputs it read. Three
append-only progress-note kinds make that dependency explicit:

* ``evidence`` declares a recorded object by its SHA-256, the namespace it belongs
  to and, for a manifest, the sorted member hashes it stands for;
* ``verdict`` records an outcome on a subject hash together with every input hash
  the reviewer read and the corrections the reviewer has already taken into
  account;
* ``correction`` declares that one recorded object is superseded by another.

Each body is canonical JSON (sorted keys, no whitespace) carrying a ``schema``
tag, so every client produces identical bytes and the hub can validate a body
before it enters the log. Parsing refuses unknown fields, malformed hashes and
oversized lists; nothing here reads the log or decides authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError

EVIDENCE_NOTE_KIND = "evidence"
"""Progress-note kind declaring one recorded object."""

VERDICT_NOTE_KIND = "verdict"
"""Progress-note kind recording a reviewer's outcome on a subject."""

CORRECTION_NOTE_KIND = "correction"
"""Progress-note kind declaring that one recorded object supersedes another."""

VERDICT_RECORD_KINDS = frozenset({EVIDENCE_NOTE_KIND, VERDICT_NOTE_KIND, CORRECTION_NOTE_KIND})
"""Progress-note kinds whose bodies this module owns."""

EVIDENCE_SCHEMA = "synapse.evidence.v1"
VERDICT_SCHEMA = "synapse.verdict.v1"
CORRECTION_SCHEMA = "synapse.correction.v1"

VERDICT_OUTCOMES = frozenset({"accepted", "rejected", "changes-required"})
"""Outcomes a verdict may record."""

MAX_MANIFEST_MEMBERS = 100_000
"""Largest number of member hashes one manifest declaration may carry."""

MAX_VERDICT_INPUTS = 1024
"""Largest number of direct input hashes one verdict may cite."""

MAX_ACKNOWLEDGED_CORRECTIONS = 1024
"""Largest number of corrections one verdict may acknowledge."""

MAX_REASON_BYTES = 2048
"""Largest UTF-8 size of a correction reason."""

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SEAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?\Z")


class VerdictRecordError(SynapseError, ValueError):
    """An evidence, verdict or correction body is malformed or out of bounds."""

    code = "verdict_record"


@dataclass(frozen=True)
class EvidenceRecord:
    """A declared recorded object; a manifest also lists its member hashes."""

    sha256: str
    namespace: str
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerdictRecord:
    """A reviewer's outcome on a subject and the inputs it was based on.

    The subject and every input are objects of ``namespace``: an object's identity
    is the pair (namespace, SHA-256), so a record in one namespace never reaches
    a verdict in another. ``reviewer_sequence`` is the reviewer's own monotone
    counter for this subject, so the latest verdict per reviewer is decided
    without arrival order.
    """

    namespace: str
    subject_sha256: str
    outcome: str
    inputs: tuple[str, ...]
    acknowledged_corrections: tuple[str, ...]
    author: str
    reviewer_sequence: int


@dataclass(frozen=True)
class CorrectionRecord:
    """A declaration that object ``corrects`` of ``namespace`` is superseded."""

    namespace: str
    corrects: str
    replacement: str
    reason: str


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(text: str, schema: str, fields: frozenset[str]) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise VerdictRecordError("record body is not JSON") from exc
    if not isinstance(payload, dict):
        raise VerdictRecordError("record body must be an object")
    if payload.get("schema") != schema:
        raise VerdictRecordError(f"record schema must be {schema}")
    if set(payload) != fields:
        raise VerdictRecordError("record fields must be exactly " + ", ".join(sorted(fields)))
    if _canonical(payload) != text:
        raise VerdictRecordError("record body is not canonical JSON")
    return payload


def _namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.match(value) is None:
        raise VerdictRecordError("namespace is invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.match(value) is None:
        raise VerdictRecordError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _digest_list(value: object, field: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VerdictRecordError(f"{field} must be a list")
    if len(value) > maximum:
        raise VerdictRecordError(f"{field} exceeds {maximum} entries")
    items = tuple(_digest(item, field) for item in value)
    if list(items) != sorted(set(items)):
        raise VerdictRecordError(f"{field} must be sorted and unique")
    return items


def format_evidence(record: EvidenceRecord) -> str:
    """Return the canonical ``evidence`` note body for ``record``."""
    return _canonical(
        {
            "members": list(record.members),
            "namespace": record.namespace,
            "schema": EVIDENCE_SCHEMA,
            "sha256": record.sha256,
        }
    )


def parse_evidence(text: str) -> EvidenceRecord:
    """Parse and bound an ``evidence`` note body."""
    payload = _load(text, EVIDENCE_SCHEMA, frozenset({"members", "namespace", "schema", "sha256"}))
    namespace = _namespace(payload["namespace"])
    sha256 = _digest(payload["sha256"], "sha256")
    members = _digest_list(payload["members"], "members", MAX_MANIFEST_MEMBERS)
    if sha256 in members:
        raise VerdictRecordError("a manifest cannot list itself as a member")
    return EvidenceRecord(sha256=sha256, namespace=namespace, members=members)


def format_verdict(record: VerdictRecord) -> str:
    """Return the canonical ``verdict`` note body for ``record``."""
    return _canonical(
        {
            "acknowledged_corrections": list(record.acknowledged_corrections),
            "author": record.author,
            "inputs": list(record.inputs),
            "namespace": record.namespace,
            "outcome": record.outcome,
            "reviewer_sequence": record.reviewer_sequence,
            "schema": VERDICT_SCHEMA,
            "subject_sha256": record.subject_sha256,
        }
    )


def parse_verdict(text: str) -> VerdictRecord:
    """Parse and bound a ``verdict`` note body."""
    payload = _load(
        text,
        VERDICT_SCHEMA,
        frozenset(
            {
                "acknowledged_corrections",
                "author",
                "inputs",
                "namespace",
                "outcome",
                "reviewer_sequence",
                "schema",
                "subject_sha256",
            }
        ),
    )
    outcome = payload["outcome"]
    if outcome not in VERDICT_OUTCOMES:
        raise VerdictRecordError("outcome must be accepted, rejected or changes-required")
    author = payload["author"]
    if not isinstance(author, str) or _SEAT.match(author) is None:
        raise VerdictRecordError("author must be a seat name")
    sequence = payload["reviewer_sequence"]
    if type(sequence) is not int or sequence < 1:
        raise VerdictRecordError("reviewer_sequence must be a positive integer")
    subject = _digest(payload["subject_sha256"], "subject_sha256")
    inputs = _digest_list(payload["inputs"], "inputs", MAX_VERDICT_INPUTS)
    if subject in inputs:
        raise VerdictRecordError("a verdict cannot cite its own subject as an input")
    acknowledged = _digest_list(
        payload["acknowledged_corrections"],
        "acknowledged_corrections",
        MAX_ACKNOWLEDGED_CORRECTIONS,
    )
    return VerdictRecord(
        namespace=_namespace(payload["namespace"]),
        subject_sha256=subject,
        outcome=str(outcome),
        inputs=inputs,
        acknowledged_corrections=acknowledged,
        author=author,
        reviewer_sequence=sequence,
    )


def format_correction(record: CorrectionRecord) -> str:
    """Return the canonical ``correction`` note body for ``record``."""
    return _canonical(
        {
            "corrects": record.corrects,
            "namespace": record.namespace,
            "reason": record.reason,
            "replacement": record.replacement,
            "schema": CORRECTION_SCHEMA,
        }
    )


def parse_correction(text: str) -> CorrectionRecord:
    """Parse and bound a ``correction`` note body."""
    payload = _load(
        text,
        CORRECTION_SCHEMA,
        frozenset({"corrects", "namespace", "reason", "replacement", "schema"}),
    )
    corrects = _digest(payload["corrects"], "corrects")
    replacement = _digest(payload["replacement"], "replacement")
    if corrects == replacement:
        raise VerdictRecordError("a correction must name a different replacement")
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise VerdictRecordError("reason must be non-empty text")
    if len(reason.encode("utf-8")) > MAX_REASON_BYTES:
        raise VerdictRecordError(f"reason exceeds {MAX_REASON_BYTES} bytes")
    return CorrectionRecord(
        namespace=_namespace(payload["namespace"]),
        corrects=corrects,
        replacement=replacement,
        reason=reason,
    )


def correction_id(record: CorrectionRecord) -> str:
    """Return the identity a verdict acknowledges: the hash of the canonical pair.

    The id covers the namespace, what is corrected and its replacement, not the
    seat or the reason, so one acknowledgement covers the same correction
    recorded by several authorised seats.
    """
    pair = _canonical(
        {
            "corrects": record.corrects,
            "namespace": record.namespace,
            "replacement": record.replacement,
        }
    )
    return hashlib.sha256(pair.encode("utf-8")).hexdigest()


def validate_record_body(kind: str, text: str) -> None:
    """Refuse a malformed body of one of :data:`VERDICT_RECORD_KINDS`.

    Raises
    ------
    VerdictRecordError
        When ``kind`` is not owned here or the body does not parse.
    """
    if kind == EVIDENCE_NOTE_KIND:
        parse_evidence(text)
    elif kind == VERDICT_NOTE_KIND:
        parse_verdict(text)
    elif kind == CORRECTION_NOTE_KIND:
        parse_correction(text)
    else:
        raise VerdictRecordError(f"unknown record kind {kind!r}")
