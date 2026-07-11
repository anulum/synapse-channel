# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded advisory guidance for the cockpit risk panel
"""Add explainable routing and resource hints to ready cockpit work.

The risk view already identifies work that is safe to pick up. This module
enriches only that ready queue with the existing deterministic semantic router
and resource bidder. The output stays advisory: it never assigns an owner,
claims a task, reserves capacity, or grants execution authority.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

from synapse_channel.core.capability_directory import (
    CapabilityDirectory,
    build_capability_directory,
)
from synapse_channel.core.resource_bidding import ResourceBidReport, recommend_resource_bids
from synapse_channel.core.semantic_routing import (
    RoutingRecommendation,
    find_task,
    recommend_agents_for_task,
)
from synapse_channel.dashboard_postmortem_feed import POSTMORTEM_PATH

MAX_GUIDANCE_TASKS: Final = 20
"""Maximum ready tasks enriched in one snapshot."""

MAX_GUIDANCE_CANDIDATES: Final = 3
"""Maximum route candidates and resource bids retained per ready task."""

GUIDANCE_TRUST_BOUNDARY: Final = (
    "Route candidates and resource bids are advisory local evidence only; they do not "
    "claim tasks, assign owners, reserve capacity, authorize execution, or certify trust."
)
"""Authority boundary carried in every guidance document."""

JsonDict = dict[str, Any]
"""Mutable JSON-object shape returned to the dashboard snapshot."""


@dataclass(frozen=True)
class TaskGuidance:
    """Bounded advisory context for one ready board task."""

    task_id: str
    route_candidates: tuple[JsonDict, ...]
    resource_bids: tuple[JsonDict, ...]
    route_fallback: str
    resource_fallback: str
    postmortem_href: str

    def to_dict(self) -> JsonDict:
        """Return a detached JSON-compatible mapping."""
        return {
            "task_id": self.task_id,
            "route_candidates": copy.deepcopy(list(self.route_candidates)),
            "resource_bids": copy.deepcopy(list(self.resource_bids)),
            "route_fallback": self.route_fallback,
            "resource_fallback": self.resource_fallback,
            "postmortem_href": self.postmortem_href,
        }


@dataclass(frozen=True)
class RiskGuidance:
    """Guidance rows plus the explicit bound and trust posture."""

    tasks: tuple[TaskGuidance, ...]
    omitted_tasks: int
    trust_boundary: str = GUIDANCE_TRUST_BOUNDARY

    def to_dict(self) -> JsonDict:
        """Return a stable JSON-compatible document."""
        return {
            "tasks": [task.to_dict() for task in self.tasks],
            "task_count": len(self.tasks),
            "omitted_tasks": self.omitted_tasks,
            "trust_boundary": self.trust_boundary,
        }


def _postmortem_href(task_id: str) -> str:
    """Return a same-origin, percent-encoded postmortem feed link."""
    return f"{POSTMORTEM_PATH}?{urlencode({'task': task_id})}"


def _clean_mappings(values: object) -> list[JsonDict]:
    """Return detached mapping rows from an arbitrary JSON value."""
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
        return []
    return [dict(value) for value in values if isinstance(value, Mapping)]


def _directory(
    manifest: Iterable[Mapping[str, Any]], state: Mapping[str, Any]
) -> CapabilityDirectory:
    """Build the existing discovery directory from snapshot inputs."""
    cards = [dict(card) for card in manifest if isinstance(card, Mapping)]
    resources = _clean_mappings(state.get("resources", []))
    return build_capability_directory(manifest=cards, resources=resources)


def _route_rows(report: RoutingRecommendation) -> tuple[JsonDict, ...]:
    """Return compact route candidates from the canonical router."""
    return tuple(
        {
            "agent": candidate.agent,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "trust": candidate.trust,
        }
        for candidate in report.candidates
    )


def _resource_rows(report: ResourceBidReport) -> tuple[JsonDict, ...]:
    """Return compact resource candidates from the canonical bidder."""
    return tuple(
        {
            "agent": candidate.agent,
            "resource_id": candidate.resource_id,
            "resource_kind": candidate.resource_kind,
            "resource_name": candidate.resource_name,
            "capacity": candidate.capacity,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "trust": candidate.trust,
        }
        for candidate in report.candidates
    )


def _task_guidance(
    task_id: str,
    board: Mapping[str, Any],
    directory: CapabilityDirectory,
) -> TaskGuidance:
    """Build one row, retaining explicit fallbacks for missing evidence."""
    task = find_task(board, task_id)
    if task is None:
        return TaskGuidance(
            task_id=task_id,
            route_candidates=(),
            resource_bids=(),
            route_fallback="ready task is absent from the board snapshot",
            resource_fallback="ready task is absent from the board snapshot",
            postmortem_href=_postmortem_href(task_id),
        )
    routes = recommend_agents_for_task(task, directory, limit=MAX_GUIDANCE_CANDIDATES)
    resources = recommend_resource_bids(task, directory, limit=MAX_GUIDANCE_CANDIDATES)
    return TaskGuidance(
        task_id=task_id,
        route_candidates=_route_rows(routes),
        resource_bids=_resource_rows(resources),
        route_fallback=routes.fallback_reason,
        resource_fallback=resources.fallback_reason,
        postmortem_href=_postmortem_href(task_id),
    )


def build_risk_guidance(
    *,
    board: Mapping[str, Any],
    manifest: Iterable[Mapping[str, Any]],
    state: Mapping[str, Any],
    safe_task_ids: Iterable[str],
) -> RiskGuidance:
    """Enrich a bounded, de-duplicated ready queue with advisory hints."""
    task_ids = tuple(dict.fromkeys(task_id.strip() for task_id in safe_task_ids if task_id.strip()))
    selected = task_ids[:MAX_GUIDANCE_TASKS]
    directory = _directory(manifest, state)
    return RiskGuidance(
        tasks=tuple(_task_guidance(task_id, board, directory) for task_id in selected),
        omitted_tasks=max(0, len(task_ids) - len(selected)),
    )
