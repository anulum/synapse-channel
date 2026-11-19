# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what a stop read back from the system of record for each listed attempt
"""Decode a ``stop_readback`` record and check it against what the hub holds.

A stop lists the attempts open in its scope. For each one the stopper creates
the attempt's ``sealed`` marker at the system of record, reads the marker back,
and records what it read in one ``stop_readback`` record:

* ``sealed``: the marker holds this attempt's ``sealed`` blob. The hub
  recomputes the blob and the marker ref from the operation, the attempt and
  the intent digest it holds. An attempt that is still open must be sealed
  first, by a ``seal`` record; one that has already ended (its executor
  reported, or the seal ended it) is closed for the stop by this record;
* ``applied``: the effect landed first, and the marker holds this attempt's
  ``applied`` blob, recomputed the same way;
* ``unsealable``: the operation's effect has no marker at a system of
  record. The hub decides this from the declaration, never from the record;
* ``unread``: the seal did not reach the system of record, or what it read
  cannot be told (``not_reached``, ``base_gone`` or ``unknown``).

Only ``sealed`` and ``applied`` close an attempt for the stop. An executor's
own report closes nothing for it: a stop trusts markers, not reports. A kept
``sealed`` read-back whose marker the hub recomputes marks the attempt sealed;
``applied``, ``unsealable`` and ``unread`` end no attempt here, and no
``stop_readback`` grants authority.

This module is pure; the hub's handler applies the ledger's checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.attempt_markers import (
    BASE_GONE,
    NOT_REACHED,
    OBJECT_ID_LENGTHS,
    UNKNOWN,
    AttemptMarker,
)

RESULT_SEALED = "sealed"
RESULT_APPLIED = "applied"
RESULT_UNSEALABLE = "unsealable"
RESULT_UNREAD = "unread"
READBACK_RESULTS = (RESULT_SEALED, RESULT_APPLIED, RESULT_UNSEALABLE, RESULT_UNREAD)
"""What a stop read back for one listed attempt."""

CLOSING_RESULTS = frozenset({RESULT_SEALED, RESULT_APPLIED})
"""The results that close an attempt for its stop."""

UNREAD_REASONS = (NOT_REACHED, BASE_GONE, UNKNOWN)
"""Why a seal's read-back is ``unread``: the seal flow's answers that show no marker."""

REFUSED_STOP_UNKNOWN = "stop_unknown"
REFUSED_READBACK_MISMATCH = "readback_mismatch"
REFUSED_READBACK_CLOSED = "readback_closed"
REFUSED_ATTEMPT_OPEN = "attempt_open"

_ATTEMPT_ID = re.compile(r"[0-9a-f]{32}")
_MARKER_REF = re.compile(r"refs/synapse/attempts/[0-9a-f]{32}")
_MARKER_FIELDS = ("marker_ref", "object_format", "marker_object")
_MAX_SEQ = 2**53


@dataclass(frozen=True)
class StopReadback:
    """What one stop read back for one of its listed attempts.

    Attributes
    ----------
    stop_seq : int
        The journal sequence of the stop that listed the attempt.
    attempt_id : str
        The listed attempt.
    result : str
        One of :data:`READBACK_RESULTS`.
    reason : str
        For ``unread``, one of :data:`UNREAD_REASONS`; otherwise empty.
    marker_ref, object_format, marker_object : str
        The marker read back; required for ``sealed`` and ``applied``, optional
        for ``unread``, absent for ``unsealable``.
    """

    stop_seq: int
    attempt_id: str
    result: str
    reason: str = ""
    marker_ref: str = ""
    object_format: str = ""
    marker_object: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Return the fields the journal row keeps; empty fields are left out."""
        payload: dict[str, Any] = {
            "stop_seq": self.stop_seq,
            "attempt_id": self.attempt_id,
            "result": self.result,
        }
        for name in ("reason", *_MARKER_FIELDS):
            value = getattr(self, name)
            if value:
                payload[name] = value
        return payload


def _marker(value: Mapping[str, Any], *, required: bool) -> tuple[str, str, str]:
    present = [name for name in _MARKER_FIELDS if name in value]
    if not present and not required:
        return "", "", ""
    if len(present) != len(_MARKER_FIELDS):
        raise ValueError("readback names marker_ref, object_format and marker_object together")
    object_format = value.get("object_format")
    if object_format not in OBJECT_ID_LENGTHS:
        raise ValueError(f"readback object_format must be one of {sorted(OBJECT_ID_LENGTHS)}")
    marker_ref, marker_object = value.get("marker_ref"), value.get("marker_object")
    if not isinstance(marker_ref, str) or _MARKER_REF.fullmatch(marker_ref) is None:
        raise ValueError("readback marker_ref has the wrong form")
    object_id = re.compile(f"[0-9a-f]{{{OBJECT_ID_LENGTHS[str(object_format)]}}}")
    if not isinstance(marker_object, str) or object_id.fullmatch(marker_object) is None:
        raise ValueError("readback marker_object has the wrong form")
    return marker_ref, str(object_format), marker_object


def decode_stop_readback(value: object) -> StopReadback:
    """Decode the ``readback`` object of a ``stop_readback`` record.

    Raises
    ------
    ValueError
        When a field is missing, of the wrong form, not expected, or not
        allowed with the stated result.
    """
    if not isinstance(value, Mapping):
        raise ValueError("readback must be an object")
    allowed = {"stop_seq", "attempt_id", "result", "reason", *_MARKER_FIELDS}
    unexpected = set(value) - allowed
    if unexpected:
        raise ValueError(f"readback names only {sorted(allowed)}")
    stop_seq = value.get("stop_seq")
    if type(stop_seq) is not int or not 0 < stop_seq < _MAX_SEQ:
        raise ValueError("readback stop_seq must be a journal sequence")
    attempt_id = value.get("attempt_id")
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValueError("readback attempt_id has the wrong form")
    result = value.get("result")
    if result not in READBACK_RESULTS:
        raise ValueError(f"readback result must be one of {list(READBACK_RESULTS)}")
    reason = value.get("reason", "")
    if result == RESULT_UNREAD:
        if reason not in UNREAD_REASONS:
            raise ValueError(f"an unread readback names its reason, one of {list(UNREAD_REASONS)}")
    elif reason != "":
        raise ValueError("only an unread readback names a reason")
    if result == RESULT_UNSEALABLE and any(name in value for name in _MARKER_FIELDS):
        raise ValueError("an unsealable readback names no marker")
    marker_ref, object_format, marker_object = _marker(value, required=result in CLOSING_RESULTS)
    return StopReadback(
        stop_seq=stop_seq,
        attempt_id=attempt_id,
        result=str(result),
        reason=str(reason),
        marker_ref=marker_ref,
        object_format=object_format,
        marker_object=marker_object,
    )


def recompute_readback_result(
    readback: StopReadback,
    *,
    operation_id: str,
    intent_sha256: str | None,
    marked: bool,
) -> str | None:
    """Recompute the result ``readback`` shows from the declaration and marker, or ``None``.

    For a marker-bearing effect, ``sealed`` and ``applied`` come from
    recomputing :class:`~synapse_channel.core.attempt_markers.AttemptMarker`
    for ``(operation_id, attempt_id, intent_sha256)`` and comparing its ref and
    blob ids against the marker fields read back, never from ``readback.result``
    alone. An effect without a marker is always ``unsealable``.
    """
    if not marked:
        return RESULT_UNSEALABLE
    if readback.result == RESULT_UNREAD:
        return RESULT_UNREAD
    if intent_sha256 is None:
        return None
    marker = AttemptMarker(operation_id, readback.attempt_id, intent_sha256)
    if readback.marker_ref != marker.ref:
        return None
    objects = marker.objects(readback.object_format)
    if readback.marker_object == objects.sealed:
        return RESULT_SEALED
    if readback.marker_object == objects.applied:
        return RESULT_APPLIED
    return None


def marker_reads_sealed(
    *,
    operation_id: str,
    attempt_id: str,
    intent_sha256: str | None,
    marker_ref: str,
    object_format: str,
    marker_object: str,
) -> bool:
    """Return whether the marker fields match the hub's recomputed ``sealed`` marker."""
    if intent_sha256 is None or object_format not in OBJECT_ID_LENGTHS:
        return False
    try:
        marker = AttemptMarker(operation_id, attempt_id, intent_sha256)
    except ValueError:
        return False
    return marker_ref == marker.ref and marker_object == marker.objects(object_format).sealed


def readback_mismatch(
    readback: StopReadback,
    *,
    operation_id: str,
    intent_sha256: str | None,
    marked: bool,
    attempt_open: bool,
) -> str | None:
    """Return why the hub cannot keep ``readback`` as stated, or ``None``.

    Parameters
    ----------
    readback : StopReadback
        The decoded record.
    operation_id : str
        The attempt's operation.
    intent_sha256 : str or None
        The intent digest the hub holds for the operation.
    marked : bool
        Whether the operation's declared effect has a marker at a system of
        record; decided by the hub from the declaration.
    attempt_open : bool
        Whether the attempt is still open in the hub's ledger.

    Returns
    -------
    str or None
        ``readback_mismatch`` when the result or the marker is not what the hub
        recomputes, ``attempt_open`` for a ``sealed`` result of an attempt
        that no seal record has ended yet, else ``None``.
    """
    recomputed = recompute_readback_result(
        readback,
        operation_id=operation_id,
        intent_sha256=intent_sha256,
        marked=marked,
    )
    if recomputed is None or recomputed != readback.result:
        return REFUSED_READBACK_MISMATCH
    if recomputed == RESULT_SEALED and attempt_open:
        return REFUSED_ATTEMPT_OPEN
    return None
