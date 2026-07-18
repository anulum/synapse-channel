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

Two card classes coexist:

* **Live cards** are ephemeral: an agent re-advertises on connect, the card is
  dropped when the agent disconnects, and a card not refreshed within a soft
  TTL is expired. A live card only means anything while its agent is live.
* **Persistent dispatch registrations** survive the disconnect: a seat
  registers with ``persist`` so automated dispatch can find it across
  sessions. A registration expires only when it is not refreshed within
  :data:`PERSISTENT_CARD_TTL_SECONDS` (24 hours); clients re-register on
  (re)connect and long-lived waiters refresh periodically (see
  ``synapse wait --capability-card``).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.capability_card_trust import CapabilityCardTrustBundle
from synapse_channel.core.capability_card_verification import (
    CapabilityCardVerification,
    CapabilityCardVerificationResult,
    verify_capability_card,
)
from synapse_channel.core.capability_contracts import CapabilityContract, normalize_contracts

DEFAULT_CARD_TTL_SECONDS = 300.0
"""Soft liveness window after which an un-refreshed card is dropped."""

PERSISTENT_CARD_TTL_SECONDS = 86_400.0
"""Refresh window for a persistent dispatch-registration card (24 hours)."""


@dataclass
class PersistentCapabilityCard:
    """A dispatch-registration card that survives its agent's disconnect.

    Unlike a live card, a persistent registration means "this project seat may
    be woken for work" — it outlives any single session and only expires when
    it is not refreshed within :data:`PERSISTENT_CARD_TTL_SECONDS`.

    Attributes
    ----------
    card : CapabilityCard
        The advertised card payload.
    dispatchable : bool
        Whether automated dispatchers may consider this seat for ready tasks.
    """

    card: CapabilityCard
    dispatchable: bool = True


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
    project : str
        Hub-resolved project namespace bound into a signed card.
    manifest_digest : str
        Optional digest of a package/tool manifest this advertisement describes.
    contracts : tuple[CapabilityContract, ...]
        Declarative input/output contracts keyed by task class.
    meta : dict[str, Any]
        Arbitrary descriptive metadata.
    signature : dict[str, Any]
        Optional Ed25519 card-signature envelope.
    verification : CapabilityCardVerification
        Explicit advisory verification result; never an execution grant.
    advertised_at : float
        Wall-clock time, in seconds, when the card was last refreshed.
    """

    agent: str
    description: str = ""
    skills: tuple[str, ...] = ()
    task_classes: tuple[str, ...] = ()
    model: str = ""
    project: str = ""
    manifest_digest: str = ""
    contracts: tuple[CapabilityContract, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)
    verification: CapabilityCardVerification = field(
        default_factory=lambda: CapabilityCardVerification(
            result=CapabilityCardVerificationResult.MISSING_SIGNATURE,
            detail="card is unsigned and remains advisory discovery",
        )
    )
    advertised_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of this card."""
        return {
            "agent": self.agent,
            "description": self.description,
            "skills": list(self.skills),
            "task_classes": list(self.task_classes),
            "model": self.model,
            "project": self.project,
            "manifest_digest": self.manifest_digest,
            "contracts": [contract.as_dict() for contract in self.contracts],
            "meta": self.meta,
            "signature": self.signature,
            "verification": self.verification.as_dict(),
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


def normalized_capability_card(
    agent: str,
    *,
    description: str = "",
    skills: Iterable[str] = (),
    task_classes: Iterable[str] = (),
    model: str = "",
    project: str = "",
    manifest_digest: str = "",
    contracts: object = (),
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable normalised fields an advertiser and hub both sign."""
    return {
        "agent": str(agent),
        "description": description.strip(),
        "skills": list(_clean_tags(skills)),
        "task_classes": list(_clean_tags(task_classes)),
        "model": model.strip(),
        "project": project.strip(),
        "manifest_digest": manifest_digest.strip(),
        "contracts": [contract.as_dict() for contract in normalize_contracts(contracts)],
        "meta": meta or {},
    }


class CapabilityRegistry:
    """One capability card per agent, exposed as a queryable manifest.

    The registry is single-threaded and synchronous; the hub owns one instance.
    Live cards are kept fresh by re-advertising and are dropped on disconnect or
    when they pass the soft TTL; persistent dispatch registrations
    (:meth:`advertise_persistent`) survive disconnects and expire only when not
    refreshed within ``persistent_ttl_seconds``.

    Parameters
    ----------
    ttl_seconds : float, optional
        Liveness window after which an un-refreshed card is expired. Defaults to
        :data:`DEFAULT_CARD_TTL_SECONDS`.
    persistent_ttl_seconds : float, optional
        Refresh window for persistent dispatch registrations. Defaults to
        :data:`PERSISTENT_CARD_TTL_SECONDS`.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_CARD_TTL_SECONDS,
        *,
        trust_bundle: CapabilityCardTrustBundle | None = None,
        persistent_ttl_seconds: float = PERSISTENT_CARD_TTL_SECONDS,
    ) -> None:
        self.cards: dict[str, CapabilityCard] = {}
        self.persistent_cards: dict[str, PersistentCapabilityCard] = {}
        self.ttl_seconds = float(ttl_seconds)
        self.persistent_ttl_seconds = float(persistent_ttl_seconds)
        self.trust_bundle = trust_bundle or CapabilityCardTrustBundle(keys={})

    def _build_card(
        self,
        agent: str,
        *,
        description: str,
        skills: Iterable[str],
        task_classes: Iterable[str],
        model: str,
        project: str,
        manifest_digest: str,
        contracts: object,
        meta: dict[str, Any] | None,
        signature: object,
        now: float,
    ) -> CapabilityCard:
        """Normalise fields and build a verified card for ``agent``."""
        candidate = normalized_capability_card(
            agent,
            description=description,
            skills=skills,
            task_classes=task_classes,
            model=model,
            project=project,
            manifest_digest=manifest_digest,
            contracts=contracts,
            meta=meta,
        )
        normalized_contracts = normalize_contracts(candidate["contracts"])
        if signature is not None:
            candidate["signature"] = signature
        verification = verify_capability_card(
            candidate,
            trust_bundle=self.trust_bundle,
            now=now,
            required_agent=str(agent),
            required_project=project.strip(),
            required_manifest_digest=manifest_digest.strip(),
        )
        return CapabilityCard(
            agent=str(agent),
            description=str(candidate["description"]),
            skills=tuple(candidate["skills"]),
            task_classes=tuple(candidate["task_classes"]),
            model=str(candidate["model"]),
            project=str(candidate["project"]),
            manifest_digest=str(candidate["manifest_digest"]),
            contracts=normalized_contracts,
            meta=dict(candidate["meta"]),
            signature=dict(signature) if isinstance(signature, Mapping) else {},
            verification=verification,
            advertised_at=now,
        )

    def advertise(
        self,
        agent: str,
        *,
        description: str = "",
        skills: Iterable[str] = (),
        task_classes: Iterable[str] = (),
        model: str = "",
        project: str = "",
        manifest_digest: str = "",
        contracts: object = (),
        meta: dict[str, Any] | None = None,
        signature: object = None,
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
        project : str, optional
            Hub-resolved project namespace. Signed cards require a non-empty value.
        manifest_digest : str, optional
            Digest of the tool/package manifest this card describes.
        contracts : object, optional
            Contract mappings or :class:`CapabilityContract` objects. Malformed
            entries are ignored and valid entries are normalised.
        meta : dict[str, Any] or None, optional
            Descriptive metadata; ``None`` becomes an empty mapping.
        signature : object, optional
            Candidate signed-card envelope. Malformed values remain visible through
            the verification result but are not echoed as a trusted mapping.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        CapabilityCard
            The stored card.
        """
        ts = time.time() if now is None else float(now)
        card = self._build_card(
            agent,
            description=description,
            skills=skills,
            task_classes=task_classes,
            model=model,
            project=project,
            manifest_digest=manifest_digest,
            contracts=contracts,
            meta=meta,
            signature=signature,
            now=ts,
        )
        self.cards[agent] = card
        return card

    def advertise_persistent(
        self,
        agent: str,
        *,
        dispatchable: bool = True,
        description: str = "",
        skills: Iterable[str] = (),
        task_classes: Iterable[str] = (),
        model: str = "",
        project: str = "",
        manifest_digest: str = "",
        contracts: object = (),
        meta: dict[str, Any] | None = None,
        signature: object = None,
        now: float | None = None,
    ) -> CapabilityCard:
        """Register or refresh a persistent dispatch card for ``agent``.

        The registration survives disconnects and expires only when it is not
        refreshed within ``persistent_ttl_seconds``. Fields and verification
        are identical to :meth:`advertise`.

        Parameters
        ----------
        agent : str
            Name of the registering agent (a project-scoped seat identity).
        dispatchable : bool, optional
            Whether automated dispatchers may consider this seat. Defaults to
            ``True``.
        now : float or None, optional
            Override for the current wall-clock time, in seconds.

        Returns
        -------
        CapabilityCard
            The stored card payload.
        """
        ts = time.time() if now is None else float(now)
        card = self._build_card(
            agent,
            description=description,
            skills=skills,
            task_classes=task_classes,
            model=model,
            project=project,
            manifest_digest=manifest_digest,
            contracts=contracts,
            meta=meta,
            signature=signature,
            now=ts,
        )
        self.persistent_cards[agent] = PersistentCapabilityCard(
            card=card, dispatchable=bool(dispatchable)
        )
        return card

    def forget(self, agent: str) -> None:
        """Drop an agent's live card, e.g. when it disconnects.

        A persistent registration is deliberately kept: it means the seat may
        be woken for work even while no interactive session is live.
        """
        self.cards.pop(agent, None)

    def forget_persistent(self, agent: str) -> None:
        """Drop an agent's persistent dispatch registration (opt-out)."""
        self.persistent_cards.pop(agent, None)

    def get(self, agent: str) -> CapabilityCard | None:
        """Return an agent's card, or ``None`` when it has none."""
        return self.cards.get(agent)

    def get_persistent(self, agent: str) -> PersistentCapabilityCard | None:
        """Return an agent's persistent registration, or ``None``."""
        return self.persistent_cards.get(agent)

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
        stale_persistent = [
            name
            for name, entry in self.persistent_cards.items()
            if (ts - entry.card.advertised_at) > self.persistent_ttl_seconds
        ]
        for name in stale_persistent:
            del self.persistent_cards[name]

    def manifest(self, now: float | None = None) -> list[dict[str, Any]]:
        """Return all live cards as dicts, sorted by agent name.

        One entry is emitted per agent. A persistent registration adds the
        additive ``persistent``/``dispatchable`` keys to that agent's entry —
        merged onto the fresher live card when both exist — so consumers see a
        single, unambiguous card per agent and legacy consumers simply ignore
        the new keys.

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
        entries: dict[str, dict[str, Any]] = {
            card.agent: card.as_dict() for card in self.cards.values()
        }
        for agent, registration in self.persistent_cards.items():
            entry = entries.get(agent) or registration.card.as_dict()
            entry["persistent"] = True
            entry["dispatchable"] = registration.dispatchable
            entries[agent] = entry
        return [entries[name] for name in sorted(entries)]

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
