# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact read-only confirmation of durable release operations
"""Confirm one release intent without replaying a mutation or reading lease absence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.protocol import SENDER_HUB, MessageType

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore

logger = logging.getLogger("synapse.hub")


@dataclass(frozen=True)
class ReleaseIntent:
    """Authenticated owner, task and exact semantic identity of one release."""

    owner: str
    task_id: str
    operation_id: str
    request_digest: str

    def __post_init__(self) -> None:
        """Reject malformed identities before sending or reading a request."""
        if (
            not isinstance(self.owner, str)
            or not self.owner.strip()
            or not isinstance(self.task_id, str)
            or not self.task_id.strip()
            or not isinstance(self.operation_id, str)
            or not 0 < len(self.operation_id) <= 128
            or "\0" in self.operation_id
            or not isinstance(self.request_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.request_digest) is None
        ):
            raise ValueError("release confirmation requires an operation key and SHA-256 digest")

    @classmethod
    def from_request(cls, request: dict[str, Any]) -> ReleaseIntent:
        """Bind an already prepared outgoing frame, including its fencing epoch."""
        return cls(
            owner=request["sender"],
            task_id=request["task_id"],
            operation_id=request["idem_key"],
            request_digest=canonical_request_digest(request),
        )

    def as_query(self) -> dict[str, str]:
        """Return the bounded private state-query extension for this intent."""
        return {
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "request_digest": self.request_digest,
        }

    def matching_receipt(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Accept only the receipt bound to this task, owner, key and digest."""
        receipt = data.get("receipt")
        if (
            data.get("task_id") != self.task_id
            or data.get("owner") != self.owner
            or data.get("release_operation_id") != self.operation_id
            or data.get("request_digest") != self.request_digest
            or not isinstance(receipt, dict)
            or receipt.get("task_id") != self.task_id
            or receipt.get("owner") != self.owner
            or receipt.get("released") is not True
        ):
            return None
        return receipt


def release_error_correlation(data: dict[str, Any]) -> dict[str, str]:
    """Correlate an ingress refusal without hashing a potentially malformed request."""
    key = data.get("idem_key")
    if data.get("type") != MessageType.RELEASE or not isinstance(key, str) or not key:
        return {}
    return {
        "release_operation_id": key,
        "task_id": str(data.get("task_id") or data.get("payload") or "").strip(),
    }


def release_reply_binding(data: dict[str, Any]) -> dict[str, str]:
    """Echo the exact keyed intent on a release verdict; leave legacy frames unchanged."""
    operation_id = data.get("idem_key")
    if not isinstance(operation_id, str) or not operation_id:
        return {}
    return {
        "release_operation_id": operation_id,
        "request_digest": canonical_request_digest(data),
    }


def read_release_confirmation(
    journal: EventStore | None, sender: str, query: object
) -> dict[str, Any]:
    """Read one owner's exact durable release and verify its transaction witnesses.

    Cache entries and lease absence never prove a commit. Missing, mismatched,
    quarantined or corrupt rows return a fixed unknown projection. Storage
    diagnostics remain in the private hub log. No state or journal row is changed.
    """
    unknown: dict[str, Any] = {"status": "unknown"}
    if not isinstance(query, dict):
        return unknown
    if not all(
        isinstance(query.get(key), str) for key in ("task_id", "operation_id", "request_digest")
    ):
        return unknown
    try:
        intent = ReleaseIntent(
            sender, query["task_id"], query["operation_id"], query["request_digest"]
        )
    except ValueError:
        return unknown
    unknown.update(intent.as_query())
    if journal is None:
        return unknown
    operation_key = f"{sender}\0{MessageType.RELEASE}\0{intent.operation_id}"
    try:
        stored = journal.get_operation(operation_key)
        if stored is None or stored.request_digest != intent.request_digest:
            return unknown
        response_hash = hashlib.sha256(
            json.dumps(
                stored.response,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        response = stored.response
        # Old keyed releases can be confirmed through their persisted digest:
        # reply-binding fields were added later and are not reconstructed claims.
        bound = {
            **response,
            "release_operation_id": intent.operation_id,
            "request_digest": intent.request_digest,
        }
        receipt = intent.matching_receipt(bound)
        if (
            response_hash != stored.response_sha256
            or response.get("sender") != SENDER_HUB
            or response.get("type") != MessageType.RELEASE_GRANTED
            or receipt is None
            or not 0 < stored.first_event_seq < stored.commit_seq
        ):
            return unknown
        witnesses = tuple(
            event
            for sequence in (stored.first_event_seq, stored.commit_seq)
            for event in journal.iter_events(after_seq=sequence - 1, through_seq=sequence)
        )
        if (
            len(witnesses) != 2
            or witnesses[0].seq != stored.first_event_seq
            or witnesses[0].kind != "release"
            or witnesses[0].payload.get("task_id") != intent.task_id
            or witnesses[1].seq != stored.commit_seq
            or witnesses[1].kind != "idempotency"
            or witnesses[1].payload.get("key") != operation_key
            or witnesses[1].payload.get("request_digest") != intent.request_digest
            or witnesses[1].payload.get("response_sha256") != response_hash
            or witnesses[1].payload.get("response") != response
            or type(witnesses[1].payload.get("first_event_seq")) is not int
            or witnesses[1].payload.get("first_event_seq") != stored.first_event_seq
            or type(witnesses[1].payload.get("commit_seq")) is not int
            or witnesses[1].payload.get("commit_seq") != stored.commit_seq
        ):
            return unknown
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        logger.exception("Durable release confirmation read failed")
        return unknown
    return {
        **intent.as_query(),
        "status": "confirmed",
        "task_id": intent.task_id,
        "owner": sender,
        "release_operation_id": intent.operation_id,
        "receipt": receipt,
        "first_event_seq": stored.first_event_seq,
        "commit_seq": stored.commit_seq,
    }
