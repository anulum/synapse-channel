# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox watermarks and pending-count projection
"""Track per-mailbox watermarks and project pending directed chats.

A mailbox is either an identity's personal mailbox (mail addressed to its name
or its project) or a role's mailbox ``role:<project>/<role>`` (mail addressed to
the role, see :mod:`synapse_channel.core.role_mailbox`). A role mailbox exists
once a connection has bound the role; any holder's acknowledgement advances it.

The watermark proves only that a mailbox receiver accepted frames through a
durable sequence. It is not evidence that a model read or acted on their bodies.
"""

from __future__ import annotations

import fnmatch
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

from synapse_channel.core.agent_liveness import waiter_owner
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.hub_address import is_hub_qualified_name
from synapse_channel.core.journal import EventKind, record_mailbox_watermark
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.role_mailbox import (
    addresses_role,
    mailbox_role,
    pending_in_mailbox,
    role_mailbox_key,
)

DEFAULT_MAX_MAILBOX_IDENTITIES = 512
"""Bounded mailboxes (personal and role) retained in one hub's pending-count projection."""

DEFAULT_MAX_ROLE_WATERMARKS = 4096
"""Role watermarks kept after their role left the bounded projection."""

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


def replayed_frames(*backlogs: Iterable[StoredEvent]) -> list[dict[str, Any]]:
    """Merge journalled chats into ``replayed`` frames, by ``seq`` and without duplicates.

    Parameters
    ----------
    *backlogs : Iterable[StoredEvent]
        Chats to replay, such as a connection's personal backlog and its roles' mail.

    Returns
    -------
    list[dict[str, Any]]
        One frame per distinct ``seq``, oldest first, marked ``replayed`` and stamped
        with its durable ``seq``; a pending receipt request is not repeated.
    """
    merged: dict[int, StoredEvent] = {}
    for backlog in backlogs:
        for event in backlog:
            merged.setdefault(event.seq, event)
    frames: list[dict[str, Any]] = []
    for seq in sorted(merged):
        frame = dict(merged[seq].payload)
        frame.pop("receipt_requested", None)
        frame["replayed"] = True
        frame["seq"] = seq
        frames.append(frame)
    return frames


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
        max_role_watermarks: int = DEFAULT_MAX_ROLE_WATERMARKS,
    ) -> None:
        self.store = store
        self.max_identities = safe_int(
            max_identities,
            default=DEFAULT_MAX_MAILBOX_IDENTITIES,
            min_value=1,
        )
        self.max_role_watermarks = safe_int(
            max_role_watermarks,
            default=DEFAULT_MAX_ROLE_WATERMARKS,
            min_value=1,
        )
        self._known: OrderedDict[str, None] = OrderedDict()
        self._watermarks: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._retired_role_watermarks: OrderedDict[str, int] = OrderedDict()
        self._restore()

    @property
    def available(self) -> bool:
        """Return whether a durable event store can support the projection."""
        return self.store is not None

    @property
    def known_identities(self) -> tuple[str, ...]:
        """Return retained mailboxes in least- to most-recent order."""
        return tuple(self._known)

    def watermark_for(self, identity: str) -> int:
        """Return the highest accepted durable chat sequence for a mailbox."""
        name = identity.strip()
        if name in self._watermarks:
            return self._watermarks[name]
        return self._retired_role_watermarks.get(name, 0)

    def has_role_mailbox(self, role: str) -> bool:
        """Return whether ``role`` has a retained mailbox in the projection."""
        return role_mailbox_key(role) in self._known

    def learn_roles(self, roles: Iterable[str]) -> tuple[str, ...]:
        """Give each bound role its mailbox and return the normalised role names.

        A personal entry with the same string as a role is dropped from the
        projection: a name that is also a bound role counts as the role. A role
        mailbox that is new starts at that entry's watermark, because mail to the
        role was counted under the plain name before roles had mailboxes, so mail
        accepted or closed under it is not replayed again. The journal keeps every
        event either way.
        """
        normalised = self._normalise_roles(roles)
        if self.store is None:
            return normalised
        for role in normalised:
            key = role_mailbox_key(role)
            fresh = key not in self._known and key not in self._retired_role_watermarks
            carried = self._adopt_plain_name(role)
            self._remember(key)
            if fresh and carried:
                self._watermarks[key] = carried
        return normalised

    def observe_chat(self, seq: int, payload: Mapping[str, Any]) -> None:
        """Fold one newly journalled chat into already-materialised counts."""
        if self.store is None:
            return
        for identity in self._target_identities(str(payload.get("target") or "all")):
            self._remember(identity)
        for identity in tuple(self._counts):
            if seq <= self._watermarks.get(identity, 0):
                continue
            if pending_in_mailbox(payload, identity):
                self._counts[identity] += 1

    def advance(
        self,
        identity: str,
        through_seq: int,
        *,
        roles: Iterable[str] = (),
        source: str,
    ) -> bool:
        """Advance one authorised cursor monotonically and journal the watermark.

        ``roles`` are the roles the advancing connection holds; they only give
        those roles their mailboxes. A cursor never advances a role's watermark.
        """
        if self.store is None:
            return False
        name = identity.strip()
        if not name:
            return False
        self.learn_roles(roles)
        self._remember(name)
        if name not in self._known:
            return False
        capped = min(
            safe_int(through_seq, default=0, min_value=0, allow_bool=False),
            self.store.max_seq(),
        )
        previous = self._watermarks.get(name, 0)
        if capped <= previous:
            return False
        if name in self._counts:
            removed = self._count_window(name, min_seq=previous + 1, max_seq=capped)
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
        """Validate an ACK against its directed chat and advance each mailbox it is in.

        The personal mailbox of ``identity`` advances when the chat was addressed to
        its name or project; the mailbox of each held role advances when the chat
        was addressed to that role.
        """
        if self.store is None or isinstance(seq, bool) or not isinstance(seq, int):
            return False
        name = identity.strip()
        if not name:
            return False
        role_names = self.learn_roles(roles)
        events = self.store.read_window(
            min_seq=seq,
            max_seq=seq,
            kinds=(EventKind.CHAT,),
            limit=1,
        )
        if not events:
            return False
        payload = events[0].payload
        advanced = False
        if pending_in_mailbox(payload, name):
            advanced = self.advance(name, seq, source="ack")
        for role in role_names:
            key = role_mailbox_key(role)
            if pending_in_mailbox(payload, key):
                advanced = self.advance(key, seq, source="ack") or advanced
        return advanced

    def pending_count(self, identity: str) -> int:
        """Return the pending count of one mailbox, projecting it when it is cold."""
        name = identity.strip()
        if self.store is None or not name:
            return 0
        self._remember(name)
        self._materialise_missing_counts()
        return self._counts.get(name, 0)

    def pending_chats(
        self,
        identity: str,
        *,
        through_seq: int,
        limit: int,
        page: int = 500,
    ) -> tuple[tuple[StoredEvent, ...], int]:
        """Return pending directed chats for a mailbox and the last sequence they cover.

        The window starts after the mailbox's watermark and ends at ``through_seq``
        (capped to the store). The predicate is the one the pending count uses. At
        most ``limit`` chats are returned; when the limit is reached the covered
        sequence is the last returned chat, so a caller that advances the watermark to
        it never skips a chat it did not see. Chats are read ``page`` rows at a time.
        """
        if self.store is None:
            return (), 0
        name = identity.strip()
        start = self.watermark_for(name)
        end = min(
            safe_int(through_seq, default=0, min_value=0, allow_bool=False),
            self.store.max_seq(),
        )
        bound = safe_int(limit, default=1, min_value=1, allow_bool=False)
        size = safe_int(page, default=500, min_value=1, allow_bool=False)
        found: list[StoredEvent] = []
        cursor = start
        while cursor < end:
            events = self.store.read_window(
                min_seq=cursor + 1,
                max_seq=end,
                kinds=(EventKind.CHAT,),
                limit=size,
            )
            if not events:
                break
            for event in events:
                if pending_in_mailbox(event.payload, name):
                    found.append(event)
                    if len(found) == bound:
                        return tuple(found), event.seq
            cursor = events[-1].seq
        return tuple(found), max(end, start)

    def role_backlog(self, roles: Iterable[str], *, limit: int) -> tuple[StoredEvent, ...]:
        """Return the chats pending in the mailboxes of ``roles``, oldest first.

        Each role's chats start after the role's watermark, however far back they
        lie, and at most ``limit`` are taken per role. A role whose pending count is
        zero is not read. A chat addressed to two held roles appears once.

        Parameters
        ----------
        roles : Iterable[str]
            The roles a reconnecting connection holds.
        limit : int
            Chats taken from each role's mailbox at most.

        Returns
        -------
        tuple[StoredEvent, ...]
            The pending role chats in sequence order.
        """
        if self.store is None:
            return ()
        end = self.store.max_seq()
        found: dict[int, StoredEvent] = {}
        for role in self.learn_roles(roles):
            key = role_mailbox_key(role)
            if self.pending_count(key) == 0:
                continue
            chats, _ = self.pending_chats(key, through_seq=end, limit=limit)
            for event in chats:
                found.setdefault(event.seq, event)
        return tuple(found[seq] for seq in sorted(found))

    def snapshot(
        self,
        online_names: Iterable[str],
        roles_of: Callable[[str], Iterable[str]],
    ) -> dict[str, int] | None:
        """Return pending counts for retained mailboxes and currently online identities.

        The roles bound by online connections (and by the identities their
        sidecars serve) get their mailboxes before the counts are projected.
        """
        if self.store is None:
            return None
        for connection in online_names:
            logical = self._logical_identity(connection)
            self.learn_roles(roles_of(connection))
            if logical != connection:
                self.learn_roles(roles_of(logical))
            self._remember(logical)
        self._materialise_missing_counts()
        return {identity: self._counts[identity] for identity in sorted(self._known)}

    def _materialise_missing_counts(self) -> None:
        """Project every cold count from one shared chat-history read."""
        missing = tuple(identity for identity in self._known if identity not in self._counts)
        if not missing:
            return
        starts = {identity: self.watermark_for(identity) + 1 for identity in missing}
        store = cast(EventStore, self.store)
        events = store.read_window(
            min_seq=min(starts.values()),
            max_seq=None,
            kinds=(EventKind.CHAT,),
        )
        counts = dict.fromkeys(missing, 0)
        exact_recipients = self._exact_recipients(missing)
        for event in events:
            payload = event.payload
            target = str(payload.get("target") or "all")
            if payload.get("channel") or not is_directed_target(target):
                continue
            sender = str(payload.get("sender") or "")
            for identity in self._recipients(target, missing, exact_recipients):
                if event.seq >= starts[identity] and sender != identity:
                    counts[identity] += 1
        self._counts.update(counts)

    @staticmethod
    def _exact_recipients(missing: Iterable[str]) -> dict[str, set[str]]:
        """Map each exact address to the cold mailboxes it reaches.

        A personal mailbox is reached by its name and its bare project; a role
        mailbox only by its role.
        """
        exact: dict[str, set[str]] = {}
        for identity in missing:
            role = mailbox_role(identity)
            addresses = (role,) if role is not None else (identity, identity.split("/", 1)[0])
            for address in addresses:
                exact.setdefault(address, set()).add(identity)
        return exact

    @staticmethod
    def _recipients(target: str, missing: Iterable[str], exact: Mapping[str, set[str]]) -> set[str]:
        """Return the cold mailboxes one directed target reaches."""
        recipients: set[str] = set()
        for part in (raw.strip() for raw in target.split(",") if raw.strip()):
            if any(marker in part for marker in _GLOB_MARKERS):
                recipients.update(identity for identity in missing if _glob_reaches(part, identity))
            else:
                recipients.update(exact.get(part, ()))
        return recipients

    def _restore(self) -> None:
        """Restore known mailboxes and the highest valid watermark of each."""
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
            role = mailbox_role(identity)
            carried = 0 if role is None else self._adopt_plain_name(role)
            self._remember(identity)
            if identity not in self._known:
                continue
            self._watermarks[identity] = max(
                self.watermark_for(identity),
                carried,
                min(through_seq, event.seq),
            )

    def _remember(self, identity: str) -> None:
        """Touch one mailbox and evict the least-recent one beyond the bound.

        A personal name equal to a retained role is not remembered: it counts as
        the role. An evicted role keeps its watermark (bounded separately), so a
        role that returns does not replay mail a holder already acknowledged.
        """
        name = identity.strip()
        if not name:
            return
        if name in self._known:
            self._known.move_to_end(name)
            return
        if mailbox_role(name) is None and role_mailbox_key(name) in self._known:
            return
        self._known[name] = None
        retired = self._retired_role_watermarks.pop(name, None)
        if retired is not None:
            self._watermarks[name] = retired
        if len(self._known) <= self.max_identities:
            return
        evicted, _ = self._known.popitem(last=False)
        watermark = self._watermarks.pop(evicted, None)
        self._counts.pop(evicted, None)
        if watermark is not None and mailbox_role(evicted) is not None:
            self._retire_role_watermark(evicted, watermark)

    def _retire_role_watermark(self, key: str, watermark: int) -> None:
        """Keep an evicted role's watermark, dropping the oldest beyond the bound."""
        self._retired_role_watermarks[key] = watermark
        self._retired_role_watermarks.move_to_end(key)
        while len(self._retired_role_watermarks) > self.max_role_watermarks:
            self._retired_role_watermarks.popitem(last=False)

    def _adopt_plain_name(self, role: str) -> int:
        """Drop the personal entry named like ``role`` and return its watermark."""
        carried = self._watermarks.get(role, 0) if role in self._known else 0
        self._forget(role)
        return carried

    def _forget(self, name: str) -> None:
        """Drop a personal entry from the projection (never from the journal)."""
        self._known.pop(name, None)
        self._watermarks.pop(name, None)
        self._counts.pop(name, None)

    @staticmethod
    def _normalise_roles(roles: Iterable[str]) -> tuple[str, ...]:
        """Return stable, unique, nonblank role names."""
        return tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))

    @staticmethod
    def _logical_identity(connection: str) -> str:
        """Map a receive-only sidecar name to the identity whose mailbox it serves."""
        return waiter_owner(connection)

    def _target_identities(self, target: str) -> tuple[str, ...]:
        """Return the mailboxes the exact parts of a target name.

        A part equal to a retained role names that role's mailbox. A hub-qualified
        part (``seat@HUB_ID``) names a seat whose mailbox lives on a peer hub, so it
        never becomes a local pending identity.
        """
        return tuple(
            dict.fromkeys(
                self._mailbox_of_part(part)
                for part in (raw.strip() for raw in target.split(","))
                if part
                and part != "all"
                and not any(marker in part for marker in _GLOB_MARKERS)
                and not is_hub_qualified_name(part)
            )
        )

    def _mailbox_of_part(self, part: str) -> str:
        """Return the role key for a part naming a retained role, else the part."""
        key = role_mailbox_key(part)
        return key if key in self._known else part

    def _count_window(self, identity: str, *, min_seq: int, max_seq: int | None) -> int:
        """Count matching directed chats inside one inclusive sequence window."""
        store = cast(EventStore, self.store)
        events = store.read_window(
            min_seq=min_seq,
            max_seq=max_seq,
            kinds=(EventKind.CHAT,),
        )
        return sum(1 for event in events if pending_in_mailbox(event.payload, identity))


def _glob_reaches(part: str, identity: str) -> bool:
    """Return whether a glob target part reaches a mailbox.

    A role mailbox is reached when its role matches the part; a personal mailbox
    when its name or its bare project does.
    """
    role = mailbox_role(identity)
    if role is not None:
        return addresses_role(part, role)
    project = identity.split("/", 1)[0]
    return fnmatch.fnmatchcase(identity, part) or fnmatch.fnmatchcase(project, part)
