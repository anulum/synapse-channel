# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed contract for optional Participant memory recall
"""Honesty-preserving records shared by Participant memory adapters.

The records deliberately separate retrieval relevance from truth. A memory
adapter may return validated, boundary, or refuted data, but unknown modes
always floor to ``boundary`` and non-finite scores become absent. The async
protocol is injected into the Participant decorator, keeping REMANENTIA and
other memory services out of bus core and provider drivers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol, cast

MemoryPresentation = Literal["validated", "boundary", "refuted"]
PRESENTATION_MODES = frozenset({"validated", "boundary", "refuted"})


def normalise_presentation(value: object) -> MemoryPresentation:
    """Return a known presentation mode, flooring every other value."""
    if isinstance(value, str) and value in PRESENTATION_MODES:
        return cast(MemoryPresentation, value)
    return "boundary"


def _required_text(name: str, value: object) -> str:
    """Validate one non-empty string field without rewriting its contents."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class MemoryHit:
    """One bounded recall hit with an explicit honesty presentation."""

    source: str
    kind: str
    score: float | None
    snippet: str
    presentation: MemoryPresentation | str
    provenance: str

    def __post_init__(self) -> None:
        """Validate scalar structure and floor unsafe honesty metadata."""
        for name in ("source", "kind", "snippet", "provenance"):
            _required_text(name, getattr(self, name))
        score = self.score
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, int | float):
                raise ValueError("score must be a number or None")
            score = float(score) if math.isfinite(float(score)) else None
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "presentation", normalise_presentation(self.presentation))


@dataclass(frozen=True)
class MemoryRecallResult:
    """A complete recall response, including an honest no-hit abstention."""

    query: str
    hits: tuple[MemoryHit, ...]
    abstained: bool
    source: str
    note: str

    def __post_init__(self) -> None:
        """Reject mutable or contradictory response shapes."""
        if not isinstance(self.query, str):
            raise ValueError("query must be a string")
        _required_text("source", self.source)
        if not isinstance(self.note, str):
            raise ValueError("note must be a string")
        if not isinstance(self.hits, tuple) or not all(
            isinstance(hit, MemoryHit) for hit in self.hits
        ):
            raise ValueError("hits must be a tuple of MemoryHit records")
        if not isinstance(self.abstained, bool):
            raise ValueError("abstained must be a boolean")
        if self.abstained != (not self.hits):
            raise ValueError("abstained must be true exactly when there are no hits")


class MemoryRecall(Protocol):
    """Injected asynchronous read side used by a memory-augmented participant."""

    async def recall(self, query: str, *, top_k: int) -> MemoryRecallResult:
        """Recall at most ``top_k`` hits for the exact operator query."""


@dataclass(frozen=True)
class MemoryPolicy:
    """Hard bounds applied around one optional recall operation."""

    timeout_seconds: float = 2.0
    top_k: int = 3
    max_chars: int = 4096

    def __post_init__(self) -> None:
        """Reject unbounded, non-finite, and boolean numeric configuration."""
        timeout = self.timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(float(timeout))
            or not 0.0 < float(timeout) <= 30.0
        ):
            raise ValueError("timeout_seconds must be finite and in (0, 30]")
        if (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or not 1 <= self.top_k <= 20
        ):
            raise ValueError("top_k must be an integer in [1, 20]")
        if (
            isinstance(self.max_chars, bool)
            or not isinstance(self.max_chars, int)
            or not 512 <= self.max_chars <= 16384
        ):
            raise ValueError("max_chars must be an integer in [512, 16384]")
        object.__setattr__(self, "timeout_seconds", float(timeout))
