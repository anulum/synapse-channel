# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only capability discovery directory
"""Manifest-backed capability directory for discovery-only routing context.

The directory joins two existing live hub surfaces: advertised capability cards
and offered resources. It deliberately stays read-only and discovery-only: an
entry helps a human, router, or adapter find a likely agent or resource, but it
does not grant permission, execute code, reserve capacity, or certify trust.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.numeric_coercion import safe_int

DISCOVERY_TRUST_BOUNDARY = (
    "Capability directory entries are discovery metadata only; they do not grant "
    "execution rights, reserve capacity, or certify agent/tool trust."
)
"""Trust boundary carried by every rendered directory snapshot."""

JsonMap = Mapping[str, Any]
"""Read-only JSON-object mapping accepted from hub snapshots."""


@dataclass(frozen=True)
class CapabilityDirectoryEntry:
    """One discoverable agent capability or resource offer.

    Parameters
    ----------
    id : str
        Stable directory identifier.
    entry_type : str
        Either ``agent`` or ``resource``.
    agent : str
        Advertising agent identity.
    label : str
        Human-readable entry label.
    description : str, optional
        Free-form capability description.
    task_classes : tuple[str, ...], optional
        Task classes advertised by an agent entry.
    skills : tuple[str, ...], optional
        Skill tags advertised by an agent entry.
    model : str, optional
        Backing model label for an agent entry.
    contracts : int, optional
        Number of formal capability contracts attached to an agent entry.
    resource_kind, resource_name : str, optional
        Resource category and concrete name for resource entries.
    capacity : int, optional
        Offered resource capacity.
    meta : dict[str, Any], optional
        Detached metadata copied from the source card or resource offer.
    """

    id: str
    entry_type: str
    agent: str
    label: str
    description: str = ""
    task_classes: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model: str = ""
    contracts: int = 0
    resource_kind: str = ""
    resource_name: str = ""
    capacity: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    trust: str = "discovery-only"

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for this directory entry."""
        return {
            "id": self.id,
            "entry_type": self.entry_type,
            "agent": self.agent,
            "label": self.label,
            "description": self.description,
            "task_classes": list(self.task_classes),
            "skills": list(self.skills),
            "model": self.model,
            "contracts": self.contracts,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
            "capacity": self.capacity,
            "meta": copy.deepcopy(self.meta),
            "trust": self.trust,
        }


@dataclass(frozen=True)
class CapabilityDirectory:
    """Read-only collection of discoverable capability and resource entries.

    Parameters
    ----------
    entries : tuple[CapabilityDirectoryEntry, ...]
        Directory entries in deterministic display order.
    trust_boundary : str, optional
        Plain-language boundary that explains what directory entries do not
        authorize.
    """

    entries: tuple[CapabilityDirectoryEntry, ...]
    trust_boundary: str = DISCOVERY_TRUST_BOUNDARY

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON object for CLI, MCP, and adapter surfaces."""
        return {
            "trust_boundary": self.trust_boundary,
            "entries": [entry.as_dict() for entry in self.entries],
        }


def _clean_strings(values: object) -> tuple[str, ...]:
    """Return stripped, de-duplicated strings from an arbitrary value."""
    if not isinstance(values, Iterable) or isinstance(values, str):
        return ()
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def _text(value: object) -> str:
    """Return ``value`` as stripped text, with blanks normalized to ``""``."""
    return str(value or "").strip()


def _meta(value: object) -> dict[str, Any]:
    """Return a detached metadata mapping from a JSON value."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _contract_count(value: object) -> int:
    """Return the number of contract mappings attached to a card."""
    if not isinstance(value, Iterable) or isinstance(value, str):
        return 0
    return sum(1 for item in value if isinstance(item, Mapping))


def _agent_entry(card: JsonMap) -> CapabilityDirectoryEntry | None:
    """Build a directory entry from one manifest card."""
    agent = _text(card.get("agent"))
    if not agent:
        return None
    return CapabilityDirectoryEntry(
        id=f"agent:{agent}",
        entry_type="agent",
        agent=agent,
        label=agent,
        description=_text(card.get("description")),
        task_classes=_clean_strings(card.get("task_classes")),
        skills=_clean_strings(card.get("skills")),
        model=_text(card.get("model")),
        contracts=_contract_count(card.get("contracts")),
        meta=_meta(card.get("meta")),
    )


def _resource_entry(resource: JsonMap) -> CapabilityDirectoryEntry | None:
    """Build a directory entry from one resource offer."""
    agent = _text(resource.get("agent"))
    kind = _text(resource.get("kind"))
    name = _text(resource.get("name"))
    if not agent or not kind or not name:
        return None
    return CapabilityDirectoryEntry(
        id=f"resource:{agent}:{kind}:{name}",
        entry_type="resource",
        agent=agent,
        label=f"{kind}/{name}",
        resource_kind=kind,
        resource_name=name,
        capacity=safe_int(resource.get("capacity") or 1, default=1, min_value=1),
        meta=_meta(resource.get("meta")),
    )


def build_capability_directory(
    *,
    manifest: Iterable[JsonMap],
    resources: Iterable[JsonMap] = (),
) -> CapabilityDirectory:
    """Build a deterministic discovery directory from live hub snapshots.

    Parameters
    ----------
    manifest : Iterable[Mapping[str, Any]]
        Capability cards from the hub manifest snapshot.
    resources : Iterable[Mapping[str, Any]], optional
        Resource offers from the hub state snapshot.

    Returns
    -------
    CapabilityDirectory
        Directory entries with agent entries first, then resource entries.
    """
    entries: list[CapabilityDirectoryEntry] = []
    entries.extend(entry for card in manifest if (entry := _agent_entry(card)) is not None)
    entries.extend(
        entry for resource in resources if (entry := _resource_entry(resource)) is not None
    )
    order = {"agent": 0, "resource": 1}
    sorted_entries = sorted(
        entries,
        key=lambda entry: (order.get(entry.entry_type, 99), entry.agent, entry.label, entry.id),
    )
    return CapabilityDirectory(entries=tuple(sorted_entries))


def filter_capability_directory(
    directory: CapabilityDirectory,
    *,
    agent: str | None = None,
    task_class: str | None = None,
    skill: str | None = None,
    resource_kind: str | None = None,
) -> CapabilityDirectory:
    """Filter a directory without changing its trust boundary.

    Parameters
    ----------
    directory : CapabilityDirectory
        Directory to filter.
    agent, task_class, skill, resource_kind : str or None, optional
        Exact-match filters. Task-class and skill filters naturally match only
        agent entries; resource-kind filters naturally match only resource
        entries.

    Returns
    -------
    CapabilityDirectory
        A new directory containing entries that satisfy every supplied filter.
    """
    agent_filter = _text(agent)
    task_filter = _text(task_class)
    skill_filter = _text(skill)
    resource_filter = _text(resource_kind)
    entries: list[CapabilityDirectoryEntry] = []
    for entry in directory.entries:
        if agent_filter and entry.agent != agent_filter:
            continue
        if task_filter and task_filter not in entry.task_classes:
            continue
        if skill_filter and skill_filter not in entry.skills:
            continue
        if resource_filter and entry.resource_kind != resource_filter:
            continue
        entries.append(entry)
    return CapabilityDirectory(entries=tuple(entries), trust_boundary=directory.trust_boundary)


def directory_to_json(directory: CapabilityDirectory) -> str:
    """Serialize ``directory`` as stable indented JSON."""
    return json.dumps(directory.as_dict(), indent=2, sort_keys=True)
