# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — advisory semantic task routing
"""Deterministic advisory routing over board tasks and capability cards.

This module is intentionally local-first: it does not call embedding services,
models, or external indexes. It scores structured signals from capability cards
first, then explainable token overlap from descriptions. The output recommends
possible agents only; it never claims a task, assigns an owner, or grants trust.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.capability_directory import CapabilityDirectory, CapabilityDirectoryEntry

ROUTING_TRUST_BOUNDARY = (
    "Semantic routing recommendations are advisory only; they do not claim tasks, "
    "assign ownership, grant permissions, or certify agent trust."
)
"""Trust boundary carried by every semantic routing recommendation."""

TASK_CLASS_SCORE = 12
"""Score added when a task token matches an advertised task class."""

SKILL_SCORE = 5
"""Score added when a task token matches an advertised skill tag."""

DESCRIPTION_TOKEN_SCORE = 3
"""Score added for each overlapping task/capability description token."""

CONTRACT_EVIDENCE_SCORE = 6
"""Score added for a matching card that carries at least one contract."""

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
class RoutingCandidate:
    """One advisory candidate agent for a board task.

    Parameters
    ----------
    agent : str
        Candidate agent identity.
    score : int
        Deterministic explainable score. Higher ranks first.
    reasons : tuple[str, ...]
        Stable reason codes behind the score.
    task_classes, skills : tuple[str, ...]
        Advertised routing metadata from the candidate's capability card.
    contracts : int
        Number of declarative contracts attached to the card.
    description, model : str
        Human-readable card details retained for inspection.
    """

    agent: str
    score: int
    reasons: tuple[str, ...]
    task_classes: tuple[str, ...]
    skills: tuple[str, ...]
    contracts: int = 0
    description: str = ""
    model: str = ""
    trust: str = "advisory-only"

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for this candidate."""
        return {
            "agent": self.agent,
            "score": self.score,
            "reasons": list(self.reasons),
            "task_classes": list(self.task_classes),
            "skills": list(self.skills),
            "contracts": self.contracts,
            "description": self.description,
            "model": self.model,
            "trust": self.trust,
        }


@dataclass(frozen=True)
class RoutingRecommendation:
    """Advisory routing recommendation for one board task.

    Parameters
    ----------
    task_id : str
        Board task id that was scored.
    query : str
        Text used for deterministic local scoring.
    candidates : tuple[RoutingCandidate, ...]
        Ranked candidate agents.
    fallback_reason : str
        Human-readable explanation when no candidate is recommended.
    task : dict[str, Any]
        Detached task snapshot used by the scorer.
    """

    task_id: str
    query: str
    candidates: tuple[RoutingCandidate, ...]
    fallback_reason: str = ""
    task: dict[str, Any] | None = None
    trust_boundary: str = ROUTING_TRUST_BOUNDARY

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for CLI and MCP surfaces."""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "fallback_reason": self.fallback_reason,
            "task": copy.deepcopy(self.task or {}),
            "trust_boundary": self.trust_boundary,
        }


def _tokens(text: str) -> frozenset[str]:
    """Return normalized content tokens from ``text``."""
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    )


def _query(task: JsonMap) -> str:
    """Return the task text used for deterministic scoring."""
    title = str(task.get("title", "") or "").strip()
    description = str(task.get("description", "") or "").strip()
    return " ".join(part for part in (title, description) if part)


def _score_entry(entry: CapabilityDirectoryEntry, task_tokens: frozenset[str]) -> RoutingCandidate:
    """Score one agent directory entry against ``task_tokens``."""
    score = 0
    reasons: list[str] = []

    for task_class in sorted(entry.task_classes):
        if task_class.lower() in task_tokens:
            score += TASK_CLASS_SCORE
            reasons.append(f"task_class:{task_class}")

    for skill in sorted(entry.skills):
        if skill.lower() in task_tokens:
            score += SKILL_SCORE
            reasons.append(f"skill:{skill}")

    description_matches = sorted(_tokens(entry.description).intersection(task_tokens))
    for token in description_matches:
        score += DESCRIPTION_TOKEN_SCORE
        reasons.append(f"description:{token}")

    if score > 0 and entry.contracts:
        score += CONTRACT_EVIDENCE_SCORE
        reasons.append("contract:evidence")

    if not reasons:
        reasons.append("no local signal match")

    return RoutingCandidate(
        agent=entry.agent,
        score=score,
        reasons=tuple(reasons),
        task_classes=entry.task_classes,
        skills=entry.skills,
        contracts=entry.contracts,
        description=entry.description,
        model=entry.model,
    )


def find_task(board: JsonMap, task_id: str) -> dict[str, Any] | None:
    """Return a detached task snapshot with ``task_id`` from a board snapshot.

    Parameters
    ----------
    board : Mapping[str, Any]
        Blackboard snapshot containing a ``tasks`` list.
    task_id : str
        Task identifier to locate.

    Returns
    -------
    dict[str, Any] or None
        The matching task snapshot, or ``None`` when absent/malformed.
    """
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        return None
    target = task_id.strip()
    for task in tasks:
        if isinstance(task, Mapping) and str(task.get("task_id", "")).strip() == target:
            return {str(key): copy.deepcopy(value) for key, value in task.items()}
    return None


def recommend_agents_for_task(
    task: JsonMap,
    directory: CapabilityDirectory,
    *,
    limit: int = 5,
    include_zero: bool = False,
) -> RoutingRecommendation:
    """Rank advertised agents for a board task using local deterministic signals.

    Parameters
    ----------
    task : Mapping[str, Any]
        Board task snapshot.
    directory : CapabilityDirectory
        Capability directory built from live manifest and resource snapshots.
    limit : int, optional
        Maximum number of candidates to return. Values below one are clamped to
        one. Defaults to ``5``.
    include_zero : bool, optional
        Include agent cards that have no matching local signal. Defaults to
        ``False``.

    Returns
    -------
    RoutingRecommendation
        Ranked, advisory-only recommendation payload.
    """
    query = _query(task)
    task_tokens = _tokens(query)
    agent_entries = [entry for entry in directory.entries if entry.entry_type == "agent"]
    task_id = str(task.get("task_id", "") or "").strip()
    if not agent_entries:
        return RoutingRecommendation(
            task_id=task_id,
            query=query,
            candidates=(),
            fallback_reason="no agent capability cards are available",
            task={str(key): copy.deepcopy(value) for key, value in task.items()},
        )

    candidates = [_score_entry(entry, task_tokens) for entry in agent_entries]
    if not include_zero:
        candidates = [candidate for candidate in candidates if candidate.score > 0]
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.agent))
    selected = tuple(candidates[: max(1, int(limit))])
    fallback = "" if selected else "no capability card matched the task text"
    return RoutingRecommendation(
        task_id=task_id,
        query=query,
        candidates=selected,
        fallback_reason=fallback,
        task={str(key): copy.deepcopy(value) for key, value in task.items()},
    )


def recommendation_to_json(recommendation: RoutingRecommendation) -> str:
    """Serialize ``recommendation`` as stable indented JSON."""
    return json.dumps(recommendation.as_dict(), indent=2, sort_keys=True)
