# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox watermarks and pending-count projection
"""Track per-identity mailbox watermarks and project pending directed chats.

The watermark proves only that a mailbox receiver accepted frames through a
durable sequence. It is not evidence that a model read or acted on their bodies.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.journal import EventKind, record_mailbox_watermark
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protocol import is_recipient

DEFAULT_MAX_MAILBOX_IDENTITIES = 512
"""Bounded identities retained in one hub's pending-count projection."""

_WAITER_SUFFIX = "-rx"
_GLOB_MARKERS = frozenset("*?[")


def parse_pending_counts(value: object) -> dict[str, int] | None:
    """Parse an additive WHO pending-count field, or report it unavailable."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    parsed: dict[str, int] = {}
    for raw_identity, raw_count in value.items():
        if not isinstance(raw_identity, str):
            continue
        identity = raw_identity.strip()
        if (
            not identity
            or isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count < 0
        ):
            continue
        parsed[identity] = raw_count
    return parsed


def format_pending_line(identity: str, count: int) -> str:
    """Return the stable human mailbox-pending sentence."""
    noun = "message" if count == 1 else "messages"
    return f"{count} undelivered {noun} pending for {identity}"


class MailboxPendingTracker:
    """Bounded, restart-safe mailbox watermark and pending-count projection."""

    def __init__(
        self,
        store: EventStore | None,
        *,
        max_identities: int = DEFAULT_MAX_MAILBOX_IDENTITIES,
    ) -> None:
        self.store = store
        self.max_identities = safe_int(
            max_identities,
            default=DEFAULT_MAX_MAILBOX_IDENTITIES,
            min_value=1,
        )
        self._known: OrderedDict[str, None] = OrderedDict()
        self._watermarks: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._roles: dict[str, tuple[str, ...]] = {}
        self._restore()

    @property
    def available(self) -> bool:
        """Return whether a durable event store can support the projection."""
        return self.store is not None

    @property
    def known_identities(self) -> tuple[str, ...]:
        """Return retained identities in least- to most-recent order."""
        return tuple(self._known)

    def watermark_for(self, identity: str) -> int:
        """Return the highest accepted durable chat sequence for ``identity``."""
        return self._watermarks.get(identity.strip(), 0)

    def observe_chat(self, seq: int, payload: Mapping[str, Any]) -> None:
        """Fold one newly journalled chat into already-materialised counts."""
        if self.store is None:
            return
        new_identities = self._target_identities(str(payload.get("target") or "all"))
        for identity in new_identities:
            self._remember(identity)
        for identity in tuple(self._counts):
            if seq <= self._watermarks.get(identity, 0):
                continue
            if self._matches_payload(payload, identity, self._roles.get(identity, ())):
                self._counts[identity] += 1

    def advance(
        self,
        identity: str,
        through_seq: int,
        *,
        roles: Iterable[str] = (),
        source: str,
    ) -> bool:
        """Advance one authorised cursor monotonically and journal the watermark."""
        if self.store is None:
            return False
        name = identity.strip()
        if not name:
            return False
        self._remember(name)
        self._set_roles(name, roles)
        capped = min(
            safe_int(through_seq, default=0, min_value=0, allow_bool=False),
            self.store.max_seq(),
        )
        previous = self._watermarks.get(name, 0)
        if capped <= previous:
            return False
        if name in self._counts:
            removed = self._count_window(
                name,
                self._roles.get(name, ()),
                min_seq=previous + 1,
                max_seq=capped,
            )
            self._counts[name] = max(0, self._counts[name] - removed)
        self._watermarks[name] = capped
        record_mailbox_watermark(
            self.store,
            identity=name,
            through_seq=capped,
            source=source,
        )
        return True

    def acknowledge(
        self,
        identity: str,
        seq: int,
        *,
        roles: Iterable[str] = (),
    ) -> bool:
        """Validate an ACK against its directed chat before advancing."""
        if self.store is None or isinstance(seq, bool) or not isinstance(seq, int):
            return False
        name = identity.strip()
        if not name:
            return False
        role_names = self._normalise_roles(roles)
        events = self.store.read_window(
            min_seq=seq,
            max_seq=seq,
            kinds=(EventKind.CHAT,),
            limit=1,
        )
        if not events or not self._matches(events[0], name, role_names):
            return False
        return self.advance(name, seq, roles=role_names, source="ack")

    def snapshot(
        self,
        online_names: Iterable[str],
        roles_of: Callable[[str], Iterable[str]],
    ) -> dict[str, int] | None:
        """Return pending counts for retained and currently online identities."""
        if self.store is None:
            return None
        online_roles: dict[str, list[str]] = {}
        for connection in online_names:
            logical = self._logical_identity(connection)
            self._remember(logical)
            combined = online_roles.setdefault(logical, [])
            combined.extend(roles_of(connection))
            if logical != connection:
                combined.extend(roles_of(logical))
        for identity, roles in online_roles.items():
            self._set_roles(identity, roles)
        for identity in tuple(self._known):
            if identity not in self._counts:
                self._counts[identity] = self._count_window(
                    identity,
                    self._roles.get(identity, ()),
                    min_seq=self._watermarks.get(identity, 0) + 1,
                    max_seq=None,
                )
        return {identity: self._counts[identity] for identity in sorted(self._known)}

    def _restore(self) -> None:
        """Restore known targets and the highest valid watermark per identity."""
        if self.store is None:
            return
        kinds = (EventKind.CHAT, EventKind.MAILBOX_WATERMARK)
        for event in self.store.iter_events(kinds=kinds):
            if event.kind == EventKind.CHAT:
                for identity in self._target_identities(str(event.payload.get("target") or "all")):
                    self._remember(identity)
                continue
            identity = str(event.payload.get("identity") or "").strip()
            if not identity:
                continue
            through_seq = safe_int(
                event.payload.get("through_seq"),
                default=0,
                min_value=0,
                allow_bool=False,
            )
            self._remember(identity)
            self._watermarks[identity] = max(
                self._watermarks.get(identity, 0),
                min(through_seq, event.seq),
            )

    def _remember(self, identity: str) -> None:
        """Touch one identity and evict the least-recent one beyond the bound."""
        name = identity.strip()
        if not name:
            return
        if name in self._known:
            self._known.move_to_end(name)
            return
        self._known[name] = None
        if len(self._known) <= self.max_identities:
            return
        evicted, _ = self._known.popitem(last=False)
        self._watermarks.pop(evicted, None)
        self._counts.pop(evicted, None)
        self._roles.pop(evicted, None)

    def _set_roles(self, identity: str, roles: Iterable[str]) -> None:
        """Bind role names and invalidate a count whose matching set changed."""
        normalised = self._normalise_roles(roles)
        if self._roles.get(identity, ()) == normalised:
            return
        self._roles[identity] = normalised
        self._counts.pop(identity, None)

    @staticmethod
    def _normalise_roles(roles: Iterable[str]) -> tuple[str, ...]:
        """Return stable, unique, nonblank role names."""
        return tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))

    @staticmethod
    def _logical_identity(connection: str) -> str:
        """Map a receive-only sidecar name to the identity whose mailbox it serves."""
        if connection.endswith(_WAITER_SUFFIX):
            return connection[: -len(_WAITER_SUFFIX)]
        return connection

    @staticmethod
    def _target_identities(target: str) -> tuple[str, ...]:
        """Return exact target parts usable as bounded identity candidates."""
        return tuple(
            dict.fromkeys(
                part
                for part in (raw.strip() for raw in target.split(","))
                if part and part != "all" and not any(marker in part for marker in _GLOB_MARKERS)
            )
        )

    def _count_window(
        self,
        identity: str,
        roles: tuple[str, ...],
        *,
        min_seq: int,
        max_seq: int | None,
    ) -> int:
        """Count matching directed chats inside one inclusive sequence window."""
        store = cast(EventStore, self.store)
        events = store.read_window(
            min_seq=min_seq,
            max_seq=max_seq,
            kinds=(EventKind.CHAT,),
        )
        return sum(1 for event in events if self._matches(event, identity, roles))

    @classmethod
    def _matches(cls, event: StoredEvent, identity: str, roles: tuple[str, ...]) -> bool:
        """Return whether one stored chat is pending for an identity."""
        return cls._matches_payload(event.payload, identity, roles)

    @staticmethod
    def _matches_payload(payload: Mapping[str, Any], identity: str, roles: tuple[str, ...]) -> bool:
        """Apply the mailbox replay predicate to one chat payload."""
        if payload.get("channel"):
            return False
        target = str(payload.get("target") or "all")
        sender = str(payload.get("sender") or "")
        return (
            sender != identity
            and is_directed_target(target)
            and is_recipient(target, identity, roles)
        )
