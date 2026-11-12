# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable task assignment recovery provenance
"""Distinguish historical pending work from assignments handled by native activity."""

from __future__ import annotations

from dataclasses import dataclass

ASSIGNMENT_ORIGIN_FIELD = "_assignment_origin"


@dataclass(frozen=True)
class AssignmentOrigin:
    """Retain the assignment generation through ordinary task status changes."""

    owner: str
    anchor: int
    legacy: bool

    def __post_init__(self) -> None:
        """Refuse unknown generations, owner coercion and invalid provenance."""
        if (
            type(self.owner) is not str
            or type(self.anchor) is not int
            or not 1 <= self.anchor <= 9223372036854775807
            or type(self.legacy) is not bool
        ):
            raise ValueError("invalid assignment origin")

    def encode(self) -> dict[str, object]:
        """Serialise trusted event metadata outside the public task record."""
        return {"version": 1, "owner": self.owner, "anchor": self.anchor, "legacy": self.legacy}

    @classmethod
    def from_value(cls, value: object) -> AssignmentOrigin | None:
        """Read exact metadata, preserving absent historical provenance as unknown."""
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "owner", "anchor", "legacy"}
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError("invalid assignment origin")
        return cls(value["owner"], value["anchor"], value["legacy"])
