# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an attempt's marker: its exact bytes, object ids, ref, and readings
"""The marker that says, at the system of record, whether one attempt's effect landed.

Every attempt has one marker ref, ``refs/synapse/attempts/<attempt id>``. The
executor's effect creates it holding the ``applied`` blob in the same atomic
change that moves the branch; a reconciler ends a silent attempt by creating it
first holding the ``sealed`` blob. The system of record accepts only the first
creation, so from then on the marker says whether the effect landed or never can.

A marker blob is five ASCII lines, each ended by one line feed, nothing else::

    synapse.attempt-marker.v1
    kind applied
    operation <operation id>
    attempt <attempt id>
    intent <intent sha256>

so anyone can compute its object id without this package. The two kinds always
differ: with the same object, Git would treat the marker as already in place and
the effect would pass. Every identifier is checked against its own pattern
before any bytes are formed.

This module is pure: it runs nothing. :mod:`synapse_channel.git.attempt_marker_changes`
runs Git and feeds its readings to :func:`change_result` and :func:`observation`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from synapse_channel.core.errors import SynapseError

MARKER_FORMAT = "synapse.attempt-marker.v1"
MARKER_NAMESPACE = "refs/synapse/attempts/"
APPLIED = "applied"
SEALED = "sealed"
KINDS = (APPLIED, SEALED)

OBJECT_ID_LENGTHS = {"sha1": 40, "sha256": 64}
"""Object formats a repository can use, with the length of their object ids."""

_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
"""The operation id rule of operation records; no space, control character or line break."""

_ATTEMPT_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")

LANDED = "landed"
LANDED_EARLIER = "landed_earlier"
OTHER_PARTY_FIRST = "other_party_first"
BASE_GONE = "base_gone"
NOT_REACHED = "not_reached"
UNKNOWN = "unknown"
CHANGE_RESULTS = (LANDED, LANDED_EARLIER, OTHER_PARTY_FIRST, BASE_GONE, NOT_REACHED, UNKNOWN)
"""What one change (an effect or a seal) did, read back from the system of record."""

LANDED_PRESENT = "landed_present"
LANDED_THEN_MOVED = "landed_then_moved"
SEALED_NEVER_LANDS = "sealed_never_lands"
MAY_STILL_LAND = "may_still_land"
PUBLISHED_OUTSIDE_PROTOCOL = "published_outside_protocol"
OBSERVATIONS = (
    LANDED_PRESENT,
    LANDED_THEN_MOVED,
    SEALED_NEVER_LANDS,
    MAY_STILL_LAND,
    PUBLISHED_OUTSIDE_PROTOCOL,
    UNKNOWN,
)
"""What an observer reads from the branch and the marker of one attempt."""


class AttemptMarkerError(SynapseError, ValueError):
    """An identifier, kind or object format that a marker cannot carry."""

    code = "attempt_marker"


@dataclass(frozen=True)
class MarkerObjects:
    """The object ids of one attempt's two marker blobs in one object format."""

    object_format: str
    applied: str
    sealed: str


def _checked(value: str, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AttemptMarkerError(f"{name} {value!r} does not match {pattern.pattern}")
    return value


@dataclass(frozen=True)
class AttemptMarker:
    """The validated identifiers one attempt's marker carries.

    Raises
    ------
    AttemptMarkerError
        When an identifier holds anything outside its own pattern, before any
        bytes are formed.
    """

    operation_id: str
    attempt_id: str
    intent_sha256: str

    def __post_init__(self) -> None:
        """Check every identifier against its own pattern before anything uses it."""
        _checked(self.operation_id, _OPERATION_ID, "operation id")
        _checked(self.attempt_id, _ATTEMPT_ID, "attempt id")
        _checked(self.intent_sha256, _DIGEST, "intent digest")

    @property
    def ref(self) -> str:
        """The marker ref, named from the validated attempt id only."""
        return MARKER_NAMESPACE + self.attempt_id

    def blob(self, kind: str) -> bytes:
        """Return the exact bytes of this attempt's ``kind`` marker."""
        if kind not in KINDS:
            raise AttemptMarkerError(f"marker kind {kind!r} is not one of {list(KINDS)}")
        lines = (
            MARKER_FORMAT,
            f"kind {kind}",
            f"operation {self.operation_id}",
            f"attempt {self.attempt_id}",
            f"intent {self.intent_sha256}",
        )
        return "".join(line + "\n" for line in lines).encode("ascii")

    def objects(self, object_format: str) -> MarkerObjects:
        """Return both marker blobs' object ids in ``object_format``."""
        return MarkerObjects(
            object_format,
            blob_object_id(self.blob(APPLIED), object_format),
            blob_object_id(self.blob(SEALED), object_format),
        )


def blob_object_id(data: bytes, object_format: str) -> str:
    """Return the Git object id of a blob holding ``data``.

    The id is the object format's hash of ``blob``, a space, the length in
    decimal, a NUL byte, then ``data``.

    Raises
    ------
    AttemptMarkerError
        When ``object_format`` is neither ``sha1`` nor ``sha256``.
    """
    if object_format not in OBJECT_ID_LENGTHS:
        raise AttemptMarkerError(
            f"object format {object_format!r} is not one of {sorted(OBJECT_ID_LENGTHS)}"
        )
    digest = hashlib.new(object_format, usedforsecurity=False)
    digest.update(b"blob %d\0" % len(data) + data)
    return digest.hexdigest()


def change_result(
    *,
    succeeded: bool,
    marker: str | None,
    branch: str | None,
    objects: MarkerObjects,
    kind: str,
    base: str,
    branch_set_to: str | None,
) -> str:
    """Return what one change did, from its exit and the marker and branch read back.

    Parameters
    ----------
    succeeded : bool
        Whether the command exited 0. It never decides on its own.
    marker, branch : str or None
        The object the marker ref holds and the commit the branch holds, read
        back after the change; ``None`` when the ref does not exist.
    objects : MarkerObjects
        The attempt's two marker blobs.
    kind : str
        The marker this change creates: ``applied`` for the effect, ``sealed``
        for the seal.
    base : str
        The declared base the effect moves the branch from.
    branch_set_to : str or None
        The commit the effect sets the branch to; ``None`` for a seal, which
        does not move the branch.
    """
    mine = objects.applied if kind == APPLIED else objects.sealed
    other = objects.sealed if kind == APPLIED else objects.applied
    if marker is not None and marker not in (mine, other):
        return UNKNOWN
    if succeeded:
        moved_as_set = branch_set_to is None or branch == branch_set_to
        return LANDED if marker == mine and moved_as_set else UNKNOWN
    if marker == mine:
        return LANDED_EARLIER
    if marker == other:
        return OTHER_PARTY_FIRST
    if branch_set_to is not None and branch != base:
        return BASE_GONE
    return NOT_REACHED


def observation(
    *,
    branch: str | None,
    marker: str | None,
    objects: MarkerObjects,
    base: str,
    candidate: str,
    contains_candidate: bool | None,
) -> str:
    """Return what an observer reads from an attempt's branch and marker.

    ``contains_candidate`` says whether the branch's commit has the candidate
    as an ancestor (only Git can tell); ``None`` when it could not be read, in
    which case a moved branch is ``unknown`` rather than guessed.
    """
    present = branch == candidate or contains_candidate is True
    if marker == objects.sealed:
        return SEALED_NEVER_LANDS
    if marker == objects.applied:
        if present:
            return LANDED_PRESENT
        return UNKNOWN if contains_candidate is None else LANDED_THEN_MOVED
    if marker is not None:
        return UNKNOWN
    if branch == base:
        return MAY_STILL_LAND
    return PUBLISHED_OUTSIDE_PROTOCOL if present else UNKNOWN
