# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the seal record: a report that an attempt was sealed at its system of record
"""Decode a ``seal`` record and check that it names exactly one attempt's seal.

An identity holding ``operation-seal`` on a task creates an attempt's
``sealed`` marker at the system of record first
(:mod:`synapse_channel.git.attempt_marker_changes`), reads it back, and then
sends this record. The hub keeps it only when it names exactly that attempt's
seal: the hub recomputes, with :mod:`synapse_channel.core.attempt_markers`, the
``sealed`` blob's id and the marker ref from the operation id, the attempt id
and the intent digest it already holds, and compares. A record that names any
other object, ref or reading is refused as ``seal_not_observed``.

This module is pure; the hub's handler applies the ledger's checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.attempt_markers import (
    CHANGE_RESULTS,
    LANDED,
    LANDED_EARLIER,
    OBJECT_ID_LENGTHS,
    AttemptMarker,
)

REFUSED_ATTEMPT_UNKNOWN = "attempt_unknown"
REFUSED_ATTEMPT_ENDED = "attempt_ended"
REFUSED_SEAL_NOT_OBSERVED = "seal_not_observed"

SEAL_OBSERVATIONS = frozenset({LANDED, LANDED_EARLIER})
"""Read-back answers that show the attempt's ``sealed`` marker in place."""

_ATTEMPT_ID = re.compile(r"[0-9a-f]{32}")
_MARKER_REF = re.compile(r"refs/synapse/attempts/[0-9a-f]{32}")


@dataclass(frozen=True)
class SealRecord:
    """What a sealer read back after creating an attempt's ``sealed`` marker.

    Attributes
    ----------
    attempt_id : str
        The sealed attempt, as the hub issued it.
    marker_ref : str
        The marker ref read back.
    object_format : str
        The repository's object format, ``sha1`` or ``sha256``.
    marker_object : str
        The object the marker ref held when read back.
    observation : str
        The read-back answer of the seal: ``landed`` or ``landed_earlier``.
    """

    attempt_id: str
    marker_ref: str
    object_format: str
    marker_object: str
    observation: str

    def to_payload(self) -> dict[str, str]:
        """Return the fields the journal row keeps."""
        return {
            "attempt_id": self.attempt_id,
            "marker_ref": self.marker_ref,
            "object_format": self.object_format,
            "marker_object": self.marker_object,
            "observation": self.observation,
        }


def _field(value: Mapping[str, Any], name: str, pattern: re.Pattern[str]) -> str:
    found = value.get(name)
    if not isinstance(found, str) or pattern.fullmatch(found) is None:
        raise ValueError(f"seal {name} has the wrong form")
    return found


def decode_seal(value: object) -> SealRecord:
    """Decode the ``seal`` object of a record; unknown fields are refused.

    Raises
    ------
    ValueError
        When a field is missing, of the wrong form, or not expected.
    """
    if not isinstance(value, Mapping):
        raise ValueError("seal must be an object")
    fields = {"attempt_id", "marker_ref", "object_format", "marker_object", "observation"}
    if set(value) != fields:
        raise ValueError(f"seal names exactly {sorted(fields)}")
    object_format = value.get("object_format")
    if object_format not in OBJECT_ID_LENGTHS:
        raise ValueError(f"seal object_format must be one of {sorted(OBJECT_ID_LENGTHS)}")
    observation = value.get("observation")
    if observation not in CHANGE_RESULTS:
        raise ValueError(f"seal observation must be one of {sorted(CHANGE_RESULTS)}")
    object_id = re.compile(f"[0-9a-f]{{{OBJECT_ID_LENGTHS[str(object_format)]}}}")
    return SealRecord(
        attempt_id=_field(value, "attempt_id", _ATTEMPT_ID),
        marker_ref=_field(value, "marker_ref", _MARKER_REF),
        object_format=str(object_format),
        marker_object=_field(value, "marker_object", object_id),
        observation=str(observation),
    )


def seal_mismatch(seal: SealRecord, *, operation_id: str, intent_sha256: str | None) -> str | None:
    """Return ``seal_not_observed`` unless ``seal`` names exactly this attempt's seal, read back.

    The ``sealed`` blob's id and the marker ref are recomputed from the
    operation id, the attempt id and the intent digest the hub holds; each was
    validated against its own pattern before it was kept.
    """
    if seal.observation not in SEAL_OBSERVATIONS or intent_sha256 is None:
        return REFUSED_SEAL_NOT_OBSERVED
    marker = AttemptMarker(operation_id, seal.attempt_id, intent_sha256)
    expected = marker.objects(seal.object_format).sealed
    if seal.marker_ref != marker.ref or seal.marker_object != expected:
        return REFUSED_SEAL_NOT_OBSERVED
    return None
