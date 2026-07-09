# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — observed capability evidence for advisory routing
"""Observed capability evidence mined from the durable coordination log.

The evidence remains deliberately modest: a positive release receipt can attach
local, provenance-preserving tokens to the agent that released a completed board
task. Routing may use those tokens as advisory evidence, but this module never
creates scores, trust grades, permissions, or assignments.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent

OBSERVED_CAPABILITY_TRUST_BOUNDARY = (
    "Observed capability evidence is provenance-preserving routing context only; "
    "it does not score, rank, certify trust, grant permissions, or assign work."
)
"""Trust boundary carried by observed capability evidence."""

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
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
        "core",
        "for",
        "from",
        "improved",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "over",
        "py",
        "q",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True)
class ObservedCapabilityEvidence:
    """One positive release-receipt signal tied to a prior board task.

    Parameters
    ----------
    agent : str
        Agent identity that authored the positive release receipt.
    task_id : str
        Prior board task id connected to the receipt.
    seq : int
        Durable event-log sequence number of the receipt progress event.
    ts : float
        Event timestamp.
    tokens : tuple[str, ...]
        Deterministic local tokens extracted from the task text and path-shaped
        receipt evidence.
    detail : str
        Original receipt note text.
    source : str, optional
        Evidence source label. Defaults to ``"release_receipt"``.
    """

    agent: str
    task_id: str
    seq: int
    ts: float
    tokens: tuple[str, ...]
    detail: str
    source: str = "release_receipt"

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for this observed signal."""
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "seq": self.seq,
            "ts": self.ts,
            "tokens": list(self.tokens),
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(frozen=True)
class ObservedCapabilityIndex:
    """Collection of observed capability evidence keyed by agent."""

    evidence: tuple[ObservedCapabilityEvidence, ...] = ()
    trust_boundary: str = OBSERVED_CAPABILITY_TRUST_BOUNDARY

    def evidence_for_agent(self, agent: str) -> tuple[ObservedCapabilityEvidence, ...]:
        """Return evidence entries for ``agent`` in deterministic order."""
        return tuple(item for item in self.evidence if item.agent == agent)

    def tokens_for_agent(self, agent: str) -> set[str]:
        """Return observed capability tokens for ``agent``."""
        tokens: set[str] = set()
        for item in self.evidence_for_agent(agent):
            tokens.update(item.tokens)
        return tokens

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for CLI, MCP, and diagnostics."""
        return {
            "trust_boundary": self.trust_boundary,
            "evidence": [item.as_dict() for item in self.evidence],
        }


def _tokens(text: str) -> set[str]:
    """Return normalized content tokens from ``text``."""
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _task_text(task: Mapping[str, Any]) -> str:
    """Return the text used to derive observed task tokens."""
    return " ".join(
        str(task.get(field, "") or "").strip() for field in ("title", "description")
    ).strip()


def _path_tokens(text: str) -> set[str]:
    """Return tokens from path-shaped fragments embedded in a receipt note."""
    tokens: set[str] = set()
    for fragment in re.split(r"[\s,;=]+", text):
        if "/" not in fragment and "." not in fragment:
            continue
        cleaned = fragment.strip(" '\"")
        for part in re.split(r"[/\\.]+", cleaned):
            tokens.update(_tokens(part))
    return tokens


def _is_positive_receipt(payload: Mapping[str, Any]) -> bool:
    """Return whether a progress payload is positive routing evidence."""
    if str(payload.get("kind", "")) != "assessment":
        return False
    text = str(payload.get("text", ""))
    lowered = text.lower()
    return (
        lowered.startswith("release receipt:")
        and "known_failures=" not in lowered
        and "epistemic_status=degraded" not in lowered
        and "epistemic_status=unsupported" not in lowered
    )


def _tasks_by_id(events: Sequence[StoredEvent]) -> dict[str, dict[str, Any]]:
    """Return the latest ledger task snapshot for each task id."""
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.kind != EventKind.LEDGER_TASK:
            continue
        task_id = str(event.payload.get("task_id", "")).strip()
        if task_id:
            tasks[task_id] = dict(event.payload)
    return tasks


def build_observed_capability_index(events: Sequence[StoredEvent]) -> ObservedCapabilityIndex:
    """Build observed capability evidence from durable event-log records.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Durable events read from an :class:`EventStore`.

    Returns
    -------
    ObservedCapabilityIndex
        Positive release-receipt evidence with task and sequence provenance.
    """
    tasks = _tasks_by_id(events)
    evidence: list[ObservedCapabilityEvidence] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS or not _is_positive_receipt(event.payload):
            continue
        task_id = str(event.payload.get("task_id", "")).strip()
        agent = str(event.payload.get("author", "")).strip()
        task = tasks.get(task_id)
        if not agent or task is None:
            continue
        text = str(event.payload.get("text", ""))
        tokens = sorted(_tokens(_task_text(task)) | _path_tokens(text))
        if not tokens:
            continue
        evidence.append(
            ObservedCapabilityEvidence(
                agent=agent,
                task_id=task_id,
                seq=event.seq,
                ts=event.ts,
                tokens=tuple(tokens),
                detail=text,
            )
        )
    evidence.sort(key=lambda item: (item.agent, item.task_id, item.seq))
    return ObservedCapabilityIndex(evidence=tuple(evidence))


def read_observed_capability_index(
    db_path: str | Path,
    *,
    key_file: str | Path | None = None,
) -> ObservedCapabilityIndex:
    """Read an event store and build observed capability evidence.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.

    Returns
    -------
    ObservedCapabilityIndex
        Evidence built from the event store.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        return build_observed_capability_index(store.read_all())
    finally:
        store.close()


def observed_capability_index_to_json(index: ObservedCapabilityIndex) -> str:
    """Serialize ``index`` as stable indented JSON."""
    return json.dumps(index.as_dict(), indent=2, sort_keys=True)
