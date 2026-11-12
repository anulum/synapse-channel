# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — current-owned work query contracts
"""Validate bounded owner/project work-list and complete-record chunk queries."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

MAX_OWNED_ITEMS = 100
MAX_OWNED_CHARS = 32768
_HEX = re.compile(r"[0-9a-f]{64}")


def is_digest(value: object, *, empty: bool = False) -> bool:
    """Require a fixed lowercase hexadecimal identifier without coercion."""
    return type(value) is str and ((empty and value == "") or _HEX.fullmatch(value) is not None)


@dataclass(frozen=True)
class OwnedQuery:
    """Bind a bounded list or one repeatable record chunk to the connected owner.

    Parameters
    ----------
    identity, hub_id : str
        Connected native identity and the previously accepted hub binding.
    project : str or None
        Project of the connected identity. None selects all current work
        belonging to this exact owner.
    mode : {"list", "read"}
        Discover opaque keys or retrieve one generation in bounded JSON chunks.
    after_key, revision : str
        Last scanned key and recipient-scoped revision from an accepted list page.
    limit : int
        Maximum scanned metadata entries, bounded to one hundred.
    key, item_version : str
        Opaque key and mutation generation returned by list.
    offset, digest, limit_chars : int, str, int
        Accepted ASCII JSON position, content SHA-256, and maximum chunk size.
    """

    identity: str
    hub_id: str = ""
    project: str | None = None
    mode: Literal["list", "read"] = "list"
    after_key: str = ""
    revision: str = ""
    limit: int = 50
    key: str = ""
    item_version: str = ""
    offset: int = 0
    digest: str = ""
    limit_chars: int = MAX_OWNED_CHARS

    def _validate_binding(self) -> None:
        """Require a bounded connected identity and its adapter-selected namespace."""
        if (
            type(self.identity) is not str
            or not self.identity
            or len(self.identity) > 256
            or any(c.isspace() for c in self.identity)
            or type(self.hub_id) is not str
            or len(self.hub_id) > 256
        ):
            raise ValueError("invalid owned work binding")
        if self.project is not None and (
            type(self.project) is not str
            or not self.project
            or "/" in self.project
            or not self.identity.startswith(self.project + "/")
        ):
            raise ValueError("invalid owned work project")

    def _validate_bounds(self) -> None:
        """Refuse coerced or unbounded page positions and content selectors."""
        if (
            self.mode not in ("list", "read")
            or type(self.limit) is not int
            or not 1 <= self.limit <= MAX_OWNED_ITEMS
            or type(self.offset) is not int
            or not 0 <= self.offset <= 9223372036854775807
            or type(self.limit_chars) is not int
            or not 1 <= self.limit_chars <= MAX_OWNED_CHARS
            or not all(
                is_digest(x, empty=True)
                for x in (self.after_key, self.revision, self.key, self.item_version, self.digest)
            )
        ):
            raise ValueError("invalid owned work query")

    def __post_init__(self) -> None:
        """Validate binding and bounds before requiring mode-exclusive arguments."""
        self._validate_binding()
        self._validate_bounds()
        if self.mode == "list":
            if (
                self.key
                or self.item_version
                or self.offset
                or self.digest
                or self.limit_chars != MAX_OWNED_CHARS
            ):
                raise ValueError("record arguments on owned list query")
            if self.after_key and (not self.hub_id or not self.revision):
                raise ValueError("unbound owned work continuation")
        else:
            self._validate_read()

    def _validate_read(self) -> None:
        """Bind an individual record read without accepting list-mode selectors."""
        if (
            not self.hub_id
            or not self.key
            or not self.item_version
            or self.after_key
            or self.revision
            or self.limit != 50
            or (self.offset and not self.digest)
        ):
            raise ValueError("unbound owned record query")

    def encode(self) -> dict[str, object]:
        """Return the exact version-one selector sent on the existing connection."""
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_value(cls, value: object) -> OwnedQuery:
        """Refuse unknown fields and versions before constructing a typed query."""
        if (
            not isinstance(value, dict)
            or set(value) != {"version", *cls.__dataclass_fields__}
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ValueError("invalid owned work query")
        return cls(**{k: v for k, v in value.items() if k != "version"})
