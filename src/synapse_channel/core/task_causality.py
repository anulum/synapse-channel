# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded content-bound parent references for task-board events
"""Typed causal-parent metadata for observed multi-hub task snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TASK_CAUSAL_PARENT_FIELD = "_causal_parent"
"""Reserved ledger-event field carrying a parent outside the task snapshot."""

MAX_CAUSAL_HUB_ID_BYTES = 512
"""Maximum UTF-8 size of a causal parent hub identifier."""

MAX_CAUSAL_EVENT_SEQ = (1 << 63) - 1
"""Largest SQLite-compatible positive event sequence accepted on the wire."""

_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class TaskCausalParent:
    """One content-bound task event that the author observed before this write."""

    hub_id: str
    seq: int
    event_fingerprint: str

    def __post_init__(self) -> None:
        """Reject malformed or unbounded references before they reach the wire."""
        if not isinstance(self.hub_id, str):
            raise ValueError("causal parent hub_id must be a string")
        hub_id = self.hub_id.strip()
        try:
            hub_id_bytes = hub_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("causal parent hub_id must be valid UTF-8") from exc
        if not hub_id or len(hub_id_bytes) > MAX_CAUSAL_HUB_ID_BYTES:
            raise ValueError("causal parent hub_id must be non-empty and at most 512 bytes")
        if (
            isinstance(self.seq, bool)
            or not isinstance(self.seq, int)
            or self.seq < 1
            or self.seq > MAX_CAUSAL_EVENT_SEQ
        ):
            raise ValueError("causal parent seq must be an integer from 1 through 2^63-1")
        fingerprint = self.event_fingerprint
        if not isinstance(fingerprint, str):
            raise ValueError("causal parent event_fingerprint must be a string")
        if len(fingerprint) != _SHA256_HEX_LENGTH or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError("causal parent event_fingerprint must be lowercase SHA-256")
        object.__setattr__(self, "hub_id", hub_id)

    @property
    def identity(self) -> tuple[str, int]:
        """Return the globally unique event identity named by this reference."""
        return (self.hub_id, self.seq)

    def to_dict(self) -> dict[str, object]:
        """Return the stable additive wire representation."""
        return {
            "hub_id": self.hub_id,
            "seq": self.seq,
            "event_fingerprint": self.event_fingerprint,
        }

    @classmethod
    def from_value(cls, value: object) -> TaskCausalParent:
        """Decode a strict mapping or an already validated parent reference."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("causal_parent must be an object")
        if set(value) != {"hub_id", "seq", "event_fingerprint"}:
            raise ValueError(
                "causal_parent must contain exactly hub_id, seq, and event_fingerprint"
            )
        hub_id = value["hub_id"]
        seq = value["seq"]
        fingerprint = value["event_fingerprint"]
        if not isinstance(hub_id, str) or not isinstance(fingerprint, str):
            raise ValueError("causal_parent hub_id and event_fingerprint must be strings")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise ValueError("causal_parent seq must be an integer from 1 through 2^63-1")
        return cls(hub_id=hub_id, seq=seq, event_fingerprint=fingerprint)


def parse_task_causal_parent(value: object) -> TaskCausalParent | None:
    """Return a validated optional parent; blank/absent values mean no assertion."""
    if value is None or value == "":
        return None
    return TaskCausalParent.from_value(value)


def parse_task_causal_parent_ref(value: str) -> TaskCausalParent:
    """Parse ``HUB_ID:SEQ:SHA256`` while allowing colons inside ``HUB_ID``."""
    hub_id, separator, tail = value.rpartition(":")
    prefix, seq_separator, raw_seq = hub_id.rpartition(":")
    if not separator or not seq_separator:
        raise ValueError("causal parent must use HUB_ID:SEQ:SHA256")
    try:
        seq = int(raw_seq)
    except ValueError as exc:
        raise ValueError("causal parent seq must be an integer from 1 through 2^63-1") from exc
    return TaskCausalParent(prefix, seq, tail)


def task_event_payload(task: Mapping[str, Any], parent: TaskCausalParent | None) -> dict[str, Any]:
    """Attach optional causal metadata without changing the projected task record."""
    payload = dict(task)
    if parent is not None:
        payload[TASK_CAUSAL_PARENT_FIELD] = parent.to_dict()
    return payload


def task_record_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip reserved event metadata from the public task snapshot."""
    return {key: value for key, value in payload.items() if key != TASK_CAUSAL_PARENT_FIELD}
