# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-bound multi-hub event identities and quarantine records
"""Content-bound identities and typed failures for observational federation."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, TypeVar

from synapse_channel.core.errors import SynapseError

_FINGERPRINT_DOMAIN = b"synapse-channel:multihub-event-fingerprint:v1\x00"
_HEX_DIGEST_LENGTH = 64


class FingerprintableHubEvent(Protocol):
    """Event fields committed by the federation collision fingerprint."""

    @property
    def hub_id(self) -> str:
        """Authoring hub identity."""
        ...  # pragma: no cover - structural typing declaration

    @property
    def seq(self) -> int:
        """Authoring hub sequence."""
        ...  # pragma: no cover - structural typing declaration

    @property
    def ts(self) -> float:
        """Exact event timestamp."""
        ...  # pragma: no cover - structural typing declaration

    @property
    def kind(self) -> str:
        """Event kind."""
        ...  # pragma: no cover - structural typing declaration

    @property
    def payload(self) -> Mapping[str, Any]:
        """Complete JSON-shaped event payload."""
        ...  # pragma: no cover - structural typing declaration


class MultiHubIntegrityError(SynapseError):
    """Base class for fail-closed multi-hub integrity failures."""

    code = "multihub_integrity"


class MultiHubEquivocationError(MultiHubIntegrityError):
    """One global event identity was presented with two content fingerprints."""

    code = "multihub_equivocation"

    def __init__(
        self,
        *,
        peer_id: str,
        seq: int,
        accepted_fingerprint: str,
        conflicting_fingerprint: str,
    ) -> None:
        self.peer_id = peer_id
        self.seq = seq
        self.accepted_fingerprint = accepted_fingerprint
        self.conflicting_fingerprint = conflicting_fingerprint
        super().__init__(f"peer {peer_id!r} equivocated at sequence {seq}")


class MultiHubSequenceError(MultiHubIntegrityError):
    """A peer snapshot violated its exclusive append-only cursor contract."""

    code = "multihub_sequence"

    def __init__(
        self,
        *,
        peer_id: str,
        after_seq: int,
        observed_seq: int,
        expected_seq: int,
    ) -> None:
        self.peer_id = peer_id
        self.after_seq = after_seq
        self.observed_seq = observed_seq
        self.expected_seq = expected_seq
        super().__init__(
            f"peer {peer_id!r} violated the append-only cursor contract: "
            f"expected sequence {expected_seq}, observed {observed_seq} "
            f"after cursor {after_seq}"
        )


class MultiHubPeerQuarantinedError(MultiHubIntegrityError):
    """An automatic poll was attempted for a persistently quarantined peer."""

    code = "multihub_peer_quarantined"

    def __init__(self, quarantine: FederationQuarantine) -> None:
        self.quarantine = quarantine
        super().__init__(
            f"peer {quarantine.peer_id!r} is quarantined after equivocation "
            f"at sequence {quarantine.seq}"
        )


@dataclass(frozen=True, slots=True)
class FederationQuarantine:
    """Digest-only evidence that prevents automatic polling of one peer.

    Parameters
    ----------
    peer_id : str
        Operator-visible peer identity attributed to the conflicting log.
    seq : int
        Reused authoring sequence.
    accepted_fingerprint : str
        SHA-256 fingerprint retained before the conflict.
    conflicting_fingerprint : str
        SHA-256 fingerprint presented for the same global identity.
    detected_at : float
        Local UNIX timestamp at which the conflict was detected.
    observer_id : str
        Local hub or process identity that detected the conflict.
    """

    peer_id: str
    seq: int
    accepted_fingerprint: str
    conflicting_fingerprint: str
    detected_at: float
    observer_id: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return bounded evidence without either event payload."""
        return {
            "peer_id": self.peer_id,
            "seq": self.seq,
            "accepted_fingerprint": self.accepted_fingerprint,
            "conflicting_fingerprint": self.conflicting_fingerprint,
            "detected_at": self.detected_at,
            "observer_id": self.observer_id,
            "status": "quarantined",
        }


TEvent = TypeVar("TEvent", bound=FingerprintableHubEvent)


def event_fingerprint(event: FingerprintableHubEvent) -> str:
    """Return the domain-separated SHA-256 fingerprint of one complete event.

    The encoding is type-tagged and length-framed. It commits the authoring hub,
    sequence, exact IEEE-754 timestamp, kind, and recursively encoded payload.
    Non-JSON payload shapes and non-finite floats fail closed.
    """
    if not isinstance(event.hub_id, str) or not event.hub_id:
        raise ValueError("multi-hub event hub_id must be a non-empty string")
    if isinstance(event.seq, bool) or not isinstance(event.seq, int) or event.seq < 1:
        raise ValueError("multi-hub event sequence must be a positive integer")
    if not isinstance(event.ts, (int, float)) or isinstance(event.ts, bool):
        raise ValueError("multi-hub event timestamp must be numeric")
    timestamp = float(event.ts)
    if not math.isfinite(timestamp):
        raise ValueError("multi-hub event timestamp must be finite")
    if not isinstance(event.kind, str) or not event.kind:
        raise ValueError("multi-hub event kind must be a non-empty string")
    if not isinstance(event.payload, Mapping):
        raise ValueError("multi-hub event payload must be a mapping")

    try:
        hub_id = event.hub_id.encode("utf-8")
        kind = event.kind.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("multi-hub event identity must be valid UTF-8") from exc

    digest = hashlib.sha256()
    digest.update(_FINGERPRINT_DOMAIN)
    digest.update(_frame(b"h", hub_id))
    digest.update(_frame(b"q", str(event.seq).encode("ascii")))
    digest.update(_frame(b"t", struct.pack(">d", timestamp)))
    digest.update(_frame(b"k", kind))
    try:
        digest.update(_frame(b"p", _encode_value(event.payload)))
    except (RecursionError, UnicodeEncodeError) as exc:
        raise ValueError("multi-hub event payload cannot be fingerprinted") from exc
    return digest.hexdigest()


def remember_content_bound_event(
    events: MutableMapping[tuple[str, int], TEvent], event: TEvent
) -> None:
    """Remember ``event`` or raise when its identity already binds other content."""
    identity = (event.hub_id, event.seq)
    existing = events.get(identity)
    if existing is None:
        event_fingerprint(event)
        events[identity] = event
        return
    accepted = event_fingerprint(existing)
    conflicting = event_fingerprint(event)
    if accepted != conflicting:
        raise MultiHubEquivocationError(
            peer_id=event.hub_id,
            seq=event.seq,
            accepted_fingerprint=accepted,
            conflicting_fingerprint=conflicting,
        )


def validate_content_bound_batch(
    events: MutableMapping[tuple[str, int], TEvent],
    batch: Sequence[TEvent],
    *,
    peer_id: str,
    after_seq: int,
) -> None:
    """Validate and stage one exclusive, contiguous append-only peer batch.

    Exact duplicates may appear anywhere and remain idempotent. New identities
    must begin at ``after_seq + 1`` and be contiguous; an unseen old sequence,
    gap, or rollback fails before the caller publishes its candidate mapping.
    """
    candidate = dict(events)
    expected_seq = after_seq + 1
    for event in batch:
        identity = (event.hub_id, event.seq)
        if event.hub_id != peer_id:
            raise ValueError("multi-hub batch event does not match its peer id")
        if event.seq < expected_seq:
            if identity not in candidate:
                raise MultiHubSequenceError(
                    peer_id=peer_id,
                    after_seq=after_seq,
                    observed_seq=event.seq,
                    expected_seq=expected_seq,
                )
            remember_content_bound_event(candidate, event)
            continue
        if event.seq > expected_seq:
            raise MultiHubSequenceError(
                peer_id=peer_id,
                after_seq=after_seq,
                observed_seq=event.seq,
                expected_seq=expected_seq,
            )
        remember_content_bound_event(candidate, event)
        expected_seq += 1
    events.clear()
    events.update(candidate)


def valid_fingerprint(value: object) -> TypeGuard[str]:
    """Return whether ``value`` is a lowercase SHA-256 hexadecimal digest."""
    return (
        isinstance(value, str)
        and len(value) == _HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _frame(tag: bytes, payload: bytes) -> bytes:
    """Return one unambiguous tagged, length-prefixed byte string."""
    return tag + len(payload).to_bytes(8, "big") + payload


def _encode_value(value: object) -> bytes:
    """Encode one JSON-shaped value without string/type ambiguities."""
    if value is None:
        return b"n"
    if value is True:
        return b"b1"
    if value is False:
        return b"b0"
    if isinstance(value, int):
        return _frame(b"i", str(value).encode("ascii"))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("multi-hub event payload floats must be finite")
        return _frame(b"f", struct.pack(">d", value))
    if isinstance(value, str):
        return _frame(b"s", value.encode("utf-8"))
    if isinstance(value, list):
        return _frame(b"l", b"".join(_frame(b"v", _encode_value(item)) for item in value))
    if isinstance(value, Mapping):
        members: list[tuple[bytes, object]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("multi-hub event payload keys must be strings")
            encoded_key = key.encode("utf-8")
            members.append((encoded_key, item))
        members.sort(key=lambda member: member[0])
        encoded = b"".join(
            _frame(b"k", key) + _frame(b"v", _encode_value(item)) for key, item in members
        )
        return _frame(b"o", encoded)
    raise ValueError(f"unsupported multi-hub event payload type: {type(value).__name__}")
