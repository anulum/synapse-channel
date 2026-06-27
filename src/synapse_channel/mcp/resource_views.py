# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only MCP resource-template renderers
"""Read-only renderers for dynamic MCP resource templates."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Any

from synapse_channel.core.semantic_routing import find_task

MCP_TEMPLATE_TRUST_BOUNDARY = (
    "MCP resource templates are read-only coordination views; they do not claim work, "
    "reserve capacity, authorize execution, or certify trust."
)
"""Boundary statement included in dynamic MCP resource-template payloads."""

JsonMap = Mapping[str, Any]
"""Read-only JSON-object mapping accepted from hub snapshots."""


def task_resource_to_json(board: JsonMap, task_id: str) -> str:
    """Return a JSON resource view for one board task.

    Parameters
    ----------
    board : Mapping[str, Any]
        Board snapshot from the hub.
    task_id : str
        Task id requested by the MCP resource URI.

    Returns
    -------
    str
        Stable JSON payload with the matching task or an empty task object.
    """
    target = task_id.strip()
    task = find_task(board, target)
    return _json(
        {
            "trust_boundary": MCP_TEMPLATE_TRUST_BOUNDARY,
            "task_id": target,
            "found": task is not None,
            "task": task or {},
        }
    )


def agent_resource_to_json(
    manifest: Iterable[JsonMap],
    resources: Iterable[JsonMap],
    agent: str,
) -> str:
    """Return a JSON resource view for one agent identity.

    Parameters
    ----------
    manifest : Iterable[Mapping[str, Any]]
        Capability cards from the hub manifest snapshot.
    resources : Iterable[Mapping[str, Any]]
        Resource offers from the hub state snapshot.
    agent : str
        Agent identity requested by the MCP resource URI.

    Returns
    -------
    str
        Stable JSON payload containing the capability card and resource offers
        for ``agent`` when present.
    """
    target = agent.strip()
    card = _first_matching(manifest, "agent", target)
    offers = tuple(
        _copy_map(resource) for resource in resources if _text(resource.get("agent")) == target
    )
    return _json(
        {
            "trust_boundary": MCP_TEMPLATE_TRUST_BOUNDARY,
            "agent": target,
            "found": card is not None or bool(offers),
            "capability_card": card or {},
            "resources": list(offers),
        }
    )


def resource_kind_resource_to_json(resources: Iterable[JsonMap], kind: str) -> str:
    """Return a JSON resource view for one resource kind.

    Parameters
    ----------
    resources : Iterable[Mapping[str, Any]]
        Resource offers from the hub state snapshot.
    kind : str
        Resource kind requested by the MCP resource URI.

    Returns
    -------
    str
        Stable JSON payload containing resource offers matching ``kind``.
    """
    target = kind.strip()
    offers = tuple(
        _copy_map(resource) for resource in resources if _text(resource.get("kind")) == target
    )
    return _json(
        {
            "trust_boundary": MCP_TEMPLATE_TRUST_BOUNDARY,
            "kind": target,
            "resources": list(offers),
        }
    )


def _first_matching(items: Iterable[JsonMap], field: str, target: str) -> dict[str, Any] | None:
    """Return the first detached mapping whose ``field`` equals ``target``."""
    for item in items:
        if _text(item.get(field)) == target:
            return _copy_map(item)
    return None


def _copy_map(item: JsonMap) -> dict[str, Any]:
    """Return a JSON-detached dict with string keys."""
    return {str(key): copy.deepcopy(value) for key, value in item.items()}


def _text(value: object) -> str:
    """Return ``value`` as stripped text, with blanks normalized to ``""``."""
    return str(value or "").strip()


def _json(payload: dict[str, Any]) -> str:
    """Serialize ``payload`` as stable indented JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)
