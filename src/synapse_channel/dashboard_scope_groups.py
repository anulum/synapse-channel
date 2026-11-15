# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the groups of namespaces a dashboard declares as scopes
"""Read the groups of namespaces one dashboard offers as scopes.

Core keeps no record of groups. A group is a convenience of this dashboard: a
name for a set of namespaces, declared by its operator in an owner-only file
(``synapse dashboard --scope-groups FILE``) and nowhere else. Nothing is
inferred, the hub is not asked, and a group grants nothing: ``group:<name>``
narrows the view to the union of its namespaces, as ``project:<namespace>``
narrows it to one.

The file is JSON::

    {"version": 1, "groups": {"harbour-fleet": ["harbour", "lighthouse"]}}

A namespace may belong to more than one group: a group is a view, and two views
may overlap. The file is refused, with the flag named, when it is not owner-only
or has another hard link, is larger than 64 KiB, is not exactly that shape,
repeats a key, names a group or namespace that a scope could not carry, or holds
an empty group or a namespace twice in one group.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from synapse_channel.core.secret_files import read_secret_file

SCOPE_GROUPS_FLAG = "--scope-groups"
"""The ``synapse dashboard`` flag that names the groups file."""

SCOPE_GROUPS_VERSION = 1
MAX_SCOPE_GROUPS_BYTES = 65_536
MAX_SCOPE_GROUPS = 256
MAX_GROUP_NAMESPACES = 256
_MAX_NAME = 256


@dataclass(frozen=True, slots=True)
class ScopeGroups:
    """The groups one dashboard declares, each a non-empty set of namespaces.

    Parameters
    ----------
    groups : Mapping[str, frozenset[str]]
        Each group's name and the namespaces it gathers.
    """

    groups: Mapping[str, frozenset[str]]

    def members(self, name: str) -> frozenset[str] | None:
        """Return the namespaces of group ``name``, or ``None`` when it is not declared."""
        return self.groups.get(name)

    def names(self) -> list[str]:
        """Return every declared group's name, in name order."""
        return sorted(self.groups)


def valid_scope_name(name: object) -> bool:
    """Return whether ``name`` can be carried by a scope: 1 to 256 printable characters, no slash.

    These are the names :func:`synapse_channel.dashboard_scope.parse_scope` accepts
    after ``project:`` or ``group:``.
    """
    return (
        isinstance(name, str)
        and 0 < len(name) <= _MAX_NAME
        and "/" not in name
        and name.isprintable()
    )


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object, refusing a key it repeats."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"{SCOPE_GROUPS_FLAG}: the file repeats the key {key!r}")
        result[key] = value
    return result


def _members(name: str, value: object) -> frozenset[str]:
    """Return one group's namespaces, refusing an empty, overlong or repeating list."""
    if not isinstance(value, list) or not 0 < len(value) <= MAX_GROUP_NAMESPACES:
        raise ValueError(
            f"{SCOPE_GROUPS_FLAG}: group {name!r} needs a list of 1 to "
            f"{MAX_GROUP_NAMESPACES} namespaces"
        )
    if not all(valid_scope_name(namespace) for namespace in value):
        raise ValueError(
            f"{SCOPE_GROUPS_FLAG}: group {name!r} names a namespace a scope cannot carry "
            "(1 to 256 printable characters, no slash)"
        )
    members = frozenset(value)
    if len(members) != len(value):
        raise ValueError(f"{SCOPE_GROUPS_FLAG}: group {name!r} names a namespace twice")
    return members


def parse_scope_groups(text: str) -> ScopeGroups:
    """Return the groups a groups file declares.

    Parameters
    ----------
    text : str
        The file's JSON text.

    Raises
    ------
    ValueError
        When the text is not the declared shape; the message names the flag.
    """
    try:
        document: object = json.loads(text, object_pairs_hook=_unique)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SCOPE_GROUPS_FLAG}: the file is not JSON") from exc
    if not isinstance(document, dict) or set(document) != {"version", "groups"}:
        raise ValueError(f"{SCOPE_GROUPS_FLAG}: the file needs exactly version and groups")
    if type(document["version"]) is not int or document["version"] != SCOPE_GROUPS_VERSION:
        raise ValueError(f"{SCOPE_GROUPS_FLAG}: version must be {SCOPE_GROUPS_VERSION}")
    entries = document["groups"]
    if not isinstance(entries, dict) or not 0 < len(entries) <= MAX_SCOPE_GROUPS:
        raise ValueError(
            f"{SCOPE_GROUPS_FLAG}: groups must map 1 to {MAX_SCOPE_GROUPS} names to namespaces"
        )
    groups: dict[str, frozenset[str]] = {}
    for name, value in entries.items():
        if not valid_scope_name(name):
            raise ValueError(
                f"{SCOPE_GROUPS_FLAG}: a group name must be 1 to 256 printable characters, no slash"
            )
        groups[name] = _members(name, value)
    return ScopeGroups(MappingProxyType(groups))


def load_scope_groups(path: str | Path) -> ScopeGroups:
    """Read and check the owner-only groups file a dashboard start names.

    Parameters
    ----------
    path : str or pathlib.Path
        The file ``--scope-groups`` names.

    Raises
    ------
    ValueError
        When the file is missing, not owner-only, linked twice, too large or not
        the declared shape; the message names the flag and never the file's content.
    """
    text = read_secret_file(
        path, flag=SCOPE_GROUPS_FLAG, require_single_link=True, limit=MAX_SCOPE_GROUPS_BYTES
    )
    return parse_scope_groups(text)
