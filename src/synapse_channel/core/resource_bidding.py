# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory resource bidding over capability directory entries
"""Read-only advisory resource bids over live capability/resource offers.

The bidding layer ranks existing resource offers for a board task. It never
reserves capacity, executes tools, mutates the board, or certifies provider
trust. Scores are deterministic local evidence only, and every reason is carried
in the result for review.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.capability_directory import (
    CapabilityDirectory,
    CapabilityDirectoryEntry,
)

RESOURCE_BID_TRUST_BOUNDARY = (
    "Resource bids are advisory directory hints only; they do not reserve capacity, "
    "authorize execution, mutate tasks, or certify provider trust."
)
"""Trust boundary carried by every resource bid report."""

RESOURCE_KIND_SCORE = 10
"""Score for a resource-kind filter matching an offer."""

TASK_CLASS_SCORE = 12
"""Score for a task token matching the provider capability task class."""

SKILL_SCORE = 5
"""Score for a task token matching the provider skill tag."""

DESCRIPTION_TOKEN_SCORE = 3
"""Score for each provider description token that overlaps task text."""

RESOURCE_TOKEN_SCORE = 2
"""Score for each resource kind/name token that overlaps task text."""

META_TOKEN_SCORE = 1
"""Score for each resource metadata token that overlaps task text."""

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "over",
        "the",
        "to",
        "with",
    }
)

JsonMap = Mapping[str, Any]
"""Read-only JSON-object mapping used for board task snapshots."""


@dataclass(frozen=True)
class ResourceBidCandidate:
    """One advisory resource-offer candidate for a board task.

    Parameters
    ----------
    agent : str
        Agent that offered the resource.
    resource_id : str
        Directory id of the resource entry.
    resource_kind, resource_name : str
        Offered resource coordinates.
    capacity : int
        Advertised available capacity from the live resource offer.
    score : int
        Deterministic explainable score. Higher ranks first.
    reasons : tuple[str, ...]
        Stable reason codes behind the score.
    task_classes, skills : tuple[str, ...]
        Provider capability metadata when a matching agent card exists.
    meta : dict[str, Any]
        Detached resource-offer metadata.
    """

    agent: str
    resource_id: str
    resource_kind: str
    resource_name: str
    capacity: int
    score: int
    reasons: tuple[str, ...]
    task_classes: tuple[str, ...]
    skills: tuple[str, ...]
    meta: dict[str, Any]
    trust: str = "advisory-only"

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for this candidate."""
        return {
            "agent": self.agent,
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
            "capacity": self.capacity,
            "score": self.score,
            "reasons": list(self.reasons),
            "task_classes": list(self.task_classes),
            "skills": list(self.skills),
            "meta": copy.deepcopy(self.meta),
            "trust": self.trust,
        }


@dataclass(frozen=True)
class ResourceBidReport:
    """Advisory resource bid report for one board task."""

    task_id: str
    query: str
    resource_kind: str
    candidates: tuple[ResourceBidCandidate, ...]
    fallback_reason: str = ""
    task: dict[str, Any] | None = None
    trust_boundary: str = RESOURCE_BID_TRUST_BOUNDARY

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for CLI and MCP surfaces."""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "resource_kind": self.resource_kind,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "fallback_reason": self.fallback_reason,
            "task": copy.deepcopy(self.task or {}),
            "trust_boundary": self.trust_boundary,
        }


def recommend_resource_bids(
    task: JsonMap,
    directory: CapabilityDirectory,
    *,
    resource_kind: str | None = None,
    limit: int = 5,
    include_zero: bool = False,
) -> ResourceBidReport:
    """Rank resource offers for a board task using local deterministic signals.

    Parameters
    ----------
    task : Mapping[str, Any]
        Board task snapshot.
    directory : CapabilityDirectory
        Capability/resource directory built from live hub snapshots.
    resource_kind : str or None, optional
        Optional exact resource-kind filter.
    limit : int, optional
        Maximum number of candidates to return; values below one are clamped to
        one.
    include_zero : bool, optional
        Include candidates with no score for diagnostics.

    Returns
    -------
    ResourceBidReport
        Ranked advisory bid report.
    """
    requested_kind = _text(resource_kind)
    query = _query(task)
    task_tokens = _tokens(query)
    resources = [entry for entry in directory.entries if entry.entry_type == "resource"]
    task_id = _text(task.get("task_id"))
    if not resources:
        return _report(
            task,
            query,
            requested_kind,
            (),
            "no resource offers are available",
            task_id=task_id,
        )

    agents = {entry.agent: entry for entry in directory.entries if entry.entry_type == "agent"}
    candidates: list[ResourceBidCandidate] = []
    for resource in resources:
        if requested_kind and resource.resource_kind != requested_kind:
            continue
        candidate = _score_resource(
            resource,
            provider=agents.get(resource.agent),
            task_tokens=task_tokens,
            requested_kind=requested_kind,
        )
        if include_zero or candidate.score > 0:
            candidates.append(candidate)

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.agent,
            candidate.resource_kind,
            candidate.resource_name,
        )
    )
    selected = tuple(candidates[: max(1, int(limit))])
    fallback = "" if selected else "no resource offer matched the task text"
    return _report(task, query, requested_kind, selected, fallback, task_id=task_id)


def resource_bid_report_to_json(report: ResourceBidReport) -> str:
    """Serialize ``report`` as stable indented JSON."""
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)


def _report(
    task: JsonMap,
    query: str,
    resource_kind: str,
    candidates: tuple[ResourceBidCandidate, ...],
    fallback_reason: str,
    *,
    task_id: str,
) -> ResourceBidReport:
    """Build a detached report object."""
    return ResourceBidReport(
        task_id=task_id,
        query=query,
        resource_kind=resource_kind,
        candidates=candidates,
        fallback_reason=fallback_reason,
        task={str(key): copy.deepcopy(value) for key, value in task.items()},
    )


def _score_resource(
    resource: CapabilityDirectoryEntry,
    *,
    provider: CapabilityDirectoryEntry | None,
    task_tokens: frozenset[str],
    requested_kind: str,
) -> ResourceBidCandidate:
    """Score one resource offer against task tokens and provider metadata."""
    score = 0
    reasons: list[str] = []
    if requested_kind and resource.resource_kind == requested_kind:
        score += RESOURCE_KIND_SCORE
        reasons.append(f"resource_kind:{resource.resource_kind}")
    if resource.capacity > 0:
        capacity_score = min(resource.capacity, 10)
        score += capacity_score
        reasons.append(f"capacity:{resource.capacity}")
    if provider is not None:
        provider_score, provider_reasons = _provider_score(provider, task_tokens)
        score += provider_score
        reasons.extend(provider_reasons)
    resource_score, resource_reasons = _resource_score(resource, task_tokens)
    score += resource_score
    reasons.extend(resource_reasons)
    return ResourceBidCandidate(
        agent=resource.agent,
        resource_id=resource.id,
        resource_kind=resource.resource_kind,
        resource_name=resource.resource_name,
        capacity=resource.capacity,
        score=score,
        reasons=tuple(reasons) if reasons else ("no local signal match",),
        task_classes=() if provider is None else provider.task_classes,
        skills=() if provider is None else provider.skills,
        meta=copy.deepcopy(resource.meta),
    )


def _provider_score(
    provider: CapabilityDirectoryEntry,
    task_tokens: frozenset[str],
) -> tuple[int, list[str]]:
    """Return score and reason codes from a provider capability card."""
    score = 0
    reasons: list[str] = []
    for task_class in sorted(provider.task_classes):
        if task_class.lower() in task_tokens:
            score += TASK_CLASS_SCORE
            reasons.append(f"task_class:{task_class}")
    for skill in sorted(provider.skills):
        if skill.lower() in task_tokens:
            score += SKILL_SCORE
            reasons.append(f"skill:{skill}")
    for token in sorted(_tokens(provider.description).intersection(task_tokens)):
        score += DESCRIPTION_TOKEN_SCORE
        reasons.append(f"description:{token}")
    return score, reasons


def _resource_score(
    resource: CapabilityDirectoryEntry,
    task_tokens: frozenset[str],
) -> tuple[int, list[str]]:
    """Return score and reason codes from resource coordinates and metadata."""
    score = 0
    reasons: list[str] = []
    resource_tokens = _tokens(f"{resource.resource_kind} {resource.resource_name}")
    for token in sorted(resource_tokens.intersection(task_tokens)):
        score += RESOURCE_TOKEN_SCORE
        reasons.append(f"resource:{token}")
    meta_tokens = _tokens(" ".join(_meta_text_values(resource.meta)))
    for token in sorted(meta_tokens.intersection(task_tokens)):
        score += META_TOKEN_SCORE
        reasons.append(f"meta:{token}")
    return score, reasons


def _meta_text_values(meta: Mapping[str, Any]) -> tuple[str, ...]:
    """Return string values from nested resource metadata."""
    values: list[str] = []
    for value in meta.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, int | float | bool):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, str | int | float | bool))
    return tuple(values)


def _query(task: JsonMap) -> str:
    """Return the task text used for deterministic scoring."""
    title = _text(task.get("title"))
    description = _text(task.get("description"))
    return " ".join(part for part in (title, description) if part)


def _tokens(text: str) -> frozenset[str]:
    """Return normalized content tokens from ``text``."""
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    )


def _text(value: object) -> str:
    """Return ``value`` as stripped text, with blanks normalized to ``""``."""
    return str(value or "").strip()
