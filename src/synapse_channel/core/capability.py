# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — agent capability cards and the hub-aggregated manifest
"""Capability cards: agent-level self-descriptions and the hub manifest.

A capability card is a small, A2A-shaped description an agent advertises about
itself — what it is, the skills it offers, and the task classes it can take —
distinct from a :class:`~synapse_channel.core.state.ResourceOffer`, which advertises a
*resource* (a model, a device) rather than the agent's own competence. The hub
keeps one card per agent in a :class:`CapabilityRegistry` and exposes the lot as
a manifest, so any agent can discover who can do what and a router can pick a
worker by task class.

Cards are ephemeral: an agent re-advertises on connect, the card is dropped when
the agent disconnects, and a card not refreshed within a soft TTL is expired.
They are never persisted — a card only means anything while its agent is live.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.capability_contracts import CapabilityContract, normalize_contracts

DEFAULT_CARD_TTL_SECONDS = 300.0
"""Soft liveness window after which an un-refreshed card is dropped."""


@dataclass
class CapabilityCard:
    """A small, A2A-shaped description an agent advertises about itself.

    Attributes
    ----------
    agent : str
        Name of the advertising agent.
    description : str
        Free-form summary of what the agent does.
    skills : tuple[str, ...]
        Capability tags the agent claims (free-form).
    task_classes : tuple[str, ...]
        Routing classes the agent can take (e.g. ``chat``, ``rule``, ``reason``),
        used to pick a worker for a task.
    model : str
        Optional model identifier backing the agent.
    contracts : tuple[CapabilityContract, ...]
        Declarative input/output contracts keyed by task class.
    meta : dict[str, Any]
        Arbitrary descriptive metadata.
    advertised_at : float
        Wall-clock time, in seconds, when the card was last refreshed.
    """

    agent: str
    description: str = ""
    skills: tuple[str, ...] = ()
    task_classes: tuple[str, ...] = ()
    model: str = ""
    contracts: tuple[CapabilityContract, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    advertised_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this card."""
        return {
            "agent": self.agent,
            "description": self.description,
            "skills": list(self.skills),
            "task_classes": list(self.task_classes),
            "model": self.model,
            "contracts": [contract.as_dict() for contract in self.contracts],
            "meta": self.meta,
            "advertised_at": self.advertised_at,
        }


def _clean_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Strip, drop blanks, and de-duplicate a tag iterable, preserving order."""
    seen: dict[str, None] = {}
    for raw in tags:
        tag = str(raw).strip()
        if tag:
            seen.setdefault(tag, None)
    return tuple(seen)


class CapabilityRegistry:
    """One capability card per agent, exposed as a queryable manifest.

    The registry is single-threaded and synchronous; the hub owns one instance.
    Cards are kept fresh by re-advertising and are dropped on disconnect or when
    they pass the soft TTL.

    Parameters
    ----------
    ttl_seconds : float, optional
        Liveness window after which an un-refreshed card is expired. Defaults to
        :data:`DEFAULT_CARD_TTL_SECONDS`.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_CARD_TTL_SECONDS) -> None:
        self.cards: dict[str, CapabilityCard] = {}
        self.ttl_seconds = float(ttl_seconds)

    def advertise(
        self,
        agent: str,
        *,
        description: str = "",
        skills: Iterable[str] = (),
        task_classes: Iterable[str] = (),
        model: str = "",
        contracts: object = (),
        meta: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> CapabilityCard:
        """Store or refresh an agent's capability card.

        Parameters
        ----------
        agent : str
            Name of the advertising agent.
        description : str, optional
            Free-form summary.
        skills : Iterable[str], optional
            Capability tags; stripped, de-duplicated, blanks dropped.
        task_classes : Iterable[str], optional
            Routing classes; stripped, de-duplicated, blanks dropped.
        model : str, optional
            Backing model identifier.
        contracts : object, optional
            Contract mappings or :class:`CapabilityContract` objects. Malformed
            entries are ignored and valid entries are normalised.
        meta : dict[str, Any] or None, optional
            Descriptive metadata; ``None`` becomes an empty mapping.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        CapabilityCard
            The stored card.
        """
        ts = time.time() if now is None else float(now)
        card = CapabilityCard(
            agent=agent,
            description=description.strip(),
            skills=_clean_tags(skills),
            task_classes=_clean_tags(task_classes),
            model=model.strip(),
            contracts=normalize_contracts(contracts),
            meta=meta or {},
            advertised_at=ts,
        )
        self.cards[agent] = card
        return card

    def forget(self, agent: str) -> None:
        """Drop an agent's card, e.g. when it disconnects."""
        self.cards.pop(agent, None)

    def get(self, agent: str) -> CapabilityCard | None:
        """Return an agent's card, or ``None`` when it has none."""
        return self.cards.get(agent)

    def expire(self, now: float | None = None) -> None:
        """Drop every card not refreshed within the TTL of ``now``."""
        ts = time.time() if now is None else float(now)
        stale = [
            name
            for name, card in self.cards.items()
            if (ts - card.advertised_at) > self.ttl_seconds
        ]
        for name in stale:
            del self.cards[name]

    def manifest(self, now: float | None = None) -> list[dict[str, Any]]:
        """Return all live cards as dicts, sorted by agent name.

        Parameters
        ----------
        now : float or None, optional
            Override for the current wall-clock time used to expire stale cards.

        Returns
        -------
        list[dict[str, Any]]
            One card mapping per live agent.
        """
        self.expire(now)
        return [card.as_dict() for card in sorted(self.cards.values(), key=lambda c: c.agent)]

    def for_task_class(self, task_class: str, now: float | None = None) -> list[str]:
        """Return the agents that advertise a given task class, sorted by name.

        Parameters
        ----------
        task_class : str
            The routing class to match against each card's ``task_classes``.
        now : float or None, optional
            Override for the current wall-clock time used to expire stale cards.

        Returns
        -------
        list[str]
            Names of live agents that can take the task class.
        """
        self.expire(now)
        return sorted(name for name, card in self.cards.items() if task_class in card.task_classes)
