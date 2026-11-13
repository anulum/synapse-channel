# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what a Git verification reads from an attempt's marker
"""Turn an attempt's branch and marker, as one ``git ls-remote`` lists them, into a verification.

A Git publication made through an attempt leaves a marker beside the branch
(:mod:`synapse_channel.core.attempt_markers`). The verifier reads both refs in
one listing and gives :func:`~synapse_channel.core.attempt_markers.observation`
the branch, the marker and whether the branch contains the candidate. That
function is the only reading table; :data:`ATTEMPT_ROWS` names the effect,
result and observation each reading becomes:

* the candidate present over an ``applied`` marker is verified;
* an ``applied`` marker on a branch that moved away, and a ``sealed`` marker,
  are an absent effect, not verified;
* the base with no marker may still land, and anything else is unknown; both
  are not checked;
* the candidate on the branch with no marker was published outside the
  protocol: the effect is observed, so an ended attempt is reconciled, but it
  is not checked, so the operation stays open for an operator to decide. A new
  attempt opened after it starts from a base the branch no longer holds and
  ends ``base_gone`` at its compare-and-swap, so no second effect lands.

``git ls-remote`` matches its patterns at the tail of a ref after a ``/``, so a
branch named, for example, ``x/refs/heads/main`` is listed as well. The listing
is therefore keyed by the exact ref names, and lines for other refs are
ignored; a line without a tab, or with an id not of the repository's format,
makes the whole answer unrecognised.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from synapse_channel.core.attempt_markers import (
    LANDED_PRESENT,
    LANDED_THEN_MOVED,
    MAY_STILL_LAND,
    OBJECT_ID_LENGTHS,
    PUBLISHED_OUTSIDE_PROTOCOL,
    SEALED_NEVER_LANDS,
    UNKNOWN,
    AttemptMarker,
)

ATTEMPT_ROWS: dict[str, tuple[str, str, str]] = {
    LANDED_PRESENT: ("observed", "verified", "attempt_landed_and_present"),
    LANDED_THEN_MOVED: ("absent", "not_verified", "attempt_landed_then_the_branch_moved"),
    SEALED_NEVER_LANDS: ("absent", "not_verified", "attempt_sealed_never_lands"),
    MAY_STILL_LAND: ("unknown", "not_checked", "attempt_may_still_land"),
    PUBLISHED_OUTSIDE_PROTOCOL: ("observed", "not_checked", "published_outside_the_protocol"),
    UNKNOWN: ("unknown", "not_checked", "attempt_state_unknown"),
}
"""Effect, result and observation for each reading of an attempt's branch and marker."""

_LOWER_HEX = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True)
class AttemptCheck:
    """The attempt whose marker a Git verification reads beside the branch.

    Attributes
    ----------
    attempt_id : str
        The attempt the hub issued: 32 lower-case hexadecimal characters.
    intent_sha256 : str
        The digest of the operation's intent; with the operation id and the
        attempt id it names the marker and its two blobs.
    base : str
        The commit the attempt's effect starts from, in the repository's
        object format.
    """

    attempt_id: str
    intent_sha256: str
    base: str

    def marker(self, operation_id: str) -> AttemptMarker:
        """Return this attempt's marker for ``operation_id``.

        Raises
        ------
        AttemptMarkerError
            When an identifier is not of its own form.
        """
        return AttemptMarker(operation_id, self.attempt_id, self.intent_sha256)


def identifier_length(commit: str, base: str) -> int:
    """Return the object id length both commits share: 40 (SHA-1) or 64 (SHA-256).

    Raises
    ------
    ValueError
        When either is not lower-case hex of one of those lengths, or the two differ.
    """
    lengths = set(OBJECT_ID_LENGTHS.values())
    for name, value in (("commit", commit), ("base", base)):
        if len(value) not in lengths or _LOWER_HEX.fullmatch(value) is None:
            raise ValueError(f"the {name} is a full commit identity of 40 or 64 lower-case hex")
    if len(commit) != len(base):
        raise ValueError("the commit and the base are of different object formats")
    return len(commit)


def listed_refs(listing: str, wanted: Sequence[str], length: int) -> dict[str, str] | None:
    """Return the ids of the ``wanted`` refs in ``git ls-remote`` output, keyed by exact name.

    Parameters
    ----------
    listing : str
        Standard output of ``git ls-remote -- REMOTE REF...``.
    wanted : sequence of str
        The full ref names asked for.
    length : int
        The object id length of the repository's format.

    Returns
    -------
    dict[str, str] or None
        Each wanted ref that is listed, with its id; refs that are not listed
        are absent from the mapping. ``None`` when a line has no tab, carries
        an id that is not ``length`` lower-case hex, or names a wanted ref twice.
    """
    found: dict[str, str] = {}
    for line in listing.splitlines():
        if not line.strip():
            continue
        oid, separator, ref = line.partition("\t")
        if not separator or len(oid) != length or _LOWER_HEX.fullmatch(oid) is None:
            return None
        if ref not in wanted:
            continue
        if ref in found:
            return None
        found[ref] = oid
    return found
