# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a release connection beside a live seat
"""Name rules for a ``<seat>-release`` connection.

A live seat holds its own name, and Core does not take that socket over. The
release sidecar is a second name that may release only that seat's claims.
It is not a seat, it is not evidence that the seat is online, and it never
creates an identity pin.
"""

from __future__ import annotations

from typing import Any

from synapse_channel.core.protocol import MessageType

RELEASE_SUFFIX = "-release"
"""Connection suffix of a release sidecar. It does not end in ``-rx``."""

_FORBIDDEN_OWNER_SUFFIXES = ("-pane-rx", "-rx", "-release")
"""A sidecar owner must be a seat, not another sidecar or a waiter."""

SIDECAR_FRAME_TYPES = frozenset(
    {
        MessageType.HEARTBEAT,
        MessageType.STATE_REQUEST,
        MessageType.RELEASE,
    }
)
"""Frames a bound release sidecar may send after it has registered."""

_HOOK_TRIGGERS = frozenset({"commit", "merge"})
"""Triggers a narrowed sidecar may present. ``manual`` is never one of them."""


def is_release_sidecar(name: str) -> bool:
    """Return whether ``name`` uses the release suffix, including a refused one."""
    return name.endswith(RELEASE_SUFFIX)


def release_owner(name: str) -> str | None:
    """Return the seat ``name`` releases for, or ``None`` when it is not that seat.

    ``None`` means either ``name`` is not a sidecar, or the remainder is empty
    or itself ends in ``-release``, ``-rx`` or ``-pane-rx``. Callers that are
    admitting a connection treat a sidecar whose owner is ``None`` as refused.
    """
    if not name.endswith(RELEASE_SUFFIX):
        return None
    owner = name[: -len(RELEASE_SUFFIX)]
    if not owner:
        return None
    for suffix in _FORBIDDEN_OWNER_SUFFIXES:
        if owner.endswith(suffix):
            return None
    return owner


def release_connection_name(owner: str) -> str:
    """Return the sidecar connection name for ``owner``."""
    return f"{owner}{RELEASE_SUFFIX}"


def sidecar_frame_allowed(msg_type: str) -> bool:
    """Return whether a bound release sidecar may send ``msg_type``."""
    return msg_type in SIDECAR_FRAME_TYPES


def acting_release(sender: str) -> tuple[str, str | None] | None:
    """Return ``(owner, via)`` for a release, or ``None`` when the name is refused.

    ``via`` is ``None`` when ``sender`` is the seat itself. A sidecar's ``via``
    is its connection name.
    """
    if not is_release_sidecar(sender):
        return sender, None
    owner = release_owner(sender)
    if owner is None:
        return None
    return owner, sender


def claims_visible_to(sender: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Return the snapshot a sender may read.

    A seat sees the snapshot unchanged. A valid sidecar sees only its owner's
    claims. A refused sidecar name returns ``None``.
    """
    if not is_release_sidecar(sender):
        return snapshot
    owner = release_owner(sender)
    if owner is None:
        return None
    claims = snapshot.get("active_claims")
    visible = (
        [
            claim
            for claim in claims
            if isinstance(claim, dict) and str(claim.get("owner") or "") == owner
        ]
        if isinstance(claims, list)
        else []
    )
    return {"active_claims": visible}


class ReleaseSidecarRoster:
    """Sockets whose admitting token names the seat, not any agent.

    The hub composes one roster beside the client registry. It is not an
    attribute of that registry: the registry constructor is already at its
    recorded size.
    """

    def __init__(self) -> None:
        self._owner_bound: set[Any] = set()

    def note(self, websocket: Any) -> None:
        """Remember that ``websocket`` was admitted by a token that names its seat."""
        self._owner_bound.add(websocket)

    def bound(self, websocket: Any) -> bool:
        """Return whether ``websocket`` was admitted by a token that names its seat."""
        return websocket in self._owner_bound

    def forget(self, websocket: Any) -> None:
        """Drop ``websocket`` when its connection closes."""
        self._owner_bound.discard(websocket)


def claim_release_mode(git: Any) -> str:
    """Return the claim's auto-release mode, treating a plain claim as manual.

    A claim with no git context, or with a mode outside ``manual``, ``commit``
    and ``merge``, is manual. The hub still runs no git; it only reads the
    mode the client stored.
    """
    if git is None:
        return "manual"
    mode = str(getattr(git, "auto_release_on", "") or "")
    if mode not in {"manual", "commit", "merge"}:
        return "manual"
    return mode


def sidecar_trigger_refusal(task_id: str, auto_release_on: str, presented: str) -> str | None:
    """Return why a narrowed sidecar may not release ``task_id``, or ``None``.

    The claim is released only when its ``auto_release_on`` equals the commit
    or merge trigger the sidecar presents. A manual claim, and a claim of
    another trigger, is refused by name.
    """
    trigger = presented.strip()
    if trigger in _HOOK_TRIGGERS and auto_release_on == trigger:
        return None
    if auto_release_on == "manual":
        return f"claim {task_id} is manual and this release sidecar may not release it"
    shown = trigger if trigger in _HOOK_TRIGGERS else "none"
    return f"claim {task_id} auto-releases on {auto_release_on}, not on {shown}"
