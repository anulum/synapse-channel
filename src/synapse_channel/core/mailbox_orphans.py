# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only view of mailboxes whose owner has gone quiet
"""Find directed mail that waits for a name nobody is using any more.

A mailbox belongs to a name. Session names rarely come back, so mail sent to an
ended session waits for ever unless someone notices it. This module reads a
journal snapshot and reports, per name, how much directed mail is pending, the
mailbox watermark, and when the name was last active. It changes nothing.

Last activity comes from the journal, not from the hub's in-memory presence
map: the latest event whose payload ``sender`` is the name (a receive-only
sidecar such as ``<name>-rx`` counts for its owner), or the latest mailbox
watermark for the name. The roles a name last held come from journalled
``role_binding`` events (a receive-only sidecar's bindings count for its owner);
a name that never bound roles, or bound them before hubs journalled bindings,
shows none.

A role's own mailbox ``role:<project>/<role>`` is known once a connection has
bound the role or a holder has acknowledged role mail. It counts the
chats addressed to the role after that watermark, belongs to the role's project,
and its last activity is its latest watermark. A plain name equal to such a role
is not listed separately: it counts as the role, and the role mailbox starts at
that name's watermark when it is higher, as the hub's projection does.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.hub_address import is_hub_qualified_name
from synapse_channel.core.journal import EventKind
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.protocol import is_recipient
from synapse_channel.core.role_mailbox import (
    addresses_role,
    mailbox_project,
    mailbox_role,
    role_mailbox_key,
)
from synapse_channel.core.waiter_identity import waiter_owner

_GLOB_MARKERS = frozenset("*?[")
_DAY = 86_400.0


@dataclass(frozen=True)
class MailboxStanding:
    """Pending directed mail and last activity of one mailbox name.

    Attributes
    ----------
    identity : str
        The mailbox name.
    project : str
        The part of the name before the first ``/``, or the whole name; for a role
        mailbox, the role's project.
    pending : int
        Directed chats after the watermark that this name has not sent itself.
    watermark : int
        Highest journalled mailbox watermark for the name (0 when none).
    oldest_pending_ts : float or None
        Timestamp of the oldest pending chat.
    last_activity_ts : float or None
        Latest journalled activity of the name, or ``None`` when it never acted.
    roles : tuple[str, ...]
        The roles the name (or its receive-only sidecar) last bound; empty when
        none was journalled, and always empty for a role mailbox.
    """

    identity: str
    project: str
    pending: int
    watermark: int
    oldest_pending_ts: float | None
    last_activity_ts: float | None
    roles: tuple[str, ...] = ()

    def dormant_for(self, now: float) -> float | None:
        """Return seconds since the last activity, or ``None`` when the name never acted."""
        if self.last_activity_ts is None:
            return None
        return max(0.0, now - self.last_activity_ts)


def project_of(identity: str) -> str:
    """Return the project of a mailbox: a name's prefix, or a role mailbox's role project."""
    return mailbox_project(identity)


def _exact_targets(target: str) -> tuple[str, ...]:
    """Return the plain, local names a target lists; globs and peer seats are left out."""
    return tuple(
        dict.fromkeys(
            part
            for part in (raw.strip() for raw in target.split(","))
            if part
            and part != "all"
            and not any(marker in part for marker in _GLOB_MARKERS)
            and not is_hub_qualified_name(part)
        )
    )


@dataclass
class _Snapshot:
    """What one journal read collects for the mailbox standings."""

    chats: list[StoredEvent] = field(default_factory=list)
    names: dict[str, None] = field(default_factory=dict)
    watermarks: dict[str, int] = field(default_factory=dict)
    activity: dict[str, float] = field(default_factory=dict)
    bindings: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def touch(self, name: str, ts: float) -> None:
        """Record activity of ``name`` at ``ts``."""
        self.activity[name] = max(self.activity.get(name, ts), ts)

    def add_chat(self, event: StoredEvent) -> None:
        """Keep a directed, channel-free chat and remember the names it lists."""
        if event.payload.get("channel"):
            return
        target = str(event.payload.get("target") or "all")
        if not is_directed_target(target):
            return
        self.chats.append(event)
        for name in _exact_targets(target):
            self.names.setdefault(name)

    def add_watermark(self, event: StoredEvent) -> None:
        """Keep the highest watermark of a name, never beyond its own event."""
        name = str(event.payload.get("identity") or "").strip()
        if not name:
            return
        through = safe_int(
            event.payload.get("through_seq"), default=0, min_value=0, allow_bool=False
        )
        self.names.setdefault(name)
        self.watermarks[name] = max(self.watermarks.get(name, 0), min(through, event.seq))
        self.touch(name, event.ts)

    def add_binding(self, event: StoredEvent) -> None:
        """Keep a connection's latest role binding and give each bound role its mailbox."""
        name = str(event.payload.get("identity") or "").strip()
        raw = event.payload.get("roles")
        if not name or not isinstance(raw, list):
            return
        roles = tuple(
            sorted({role.strip() for role in raw if isinstance(role, str) and role.strip()})
        )
        self.bindings[name] = roles
        for role in roles:
            self.names.setdefault(role_mailbox_key(role))
        self.touch(waiter_owner(name), event.ts)

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` and its receive-only sidecars last bound."""
        if mailbox_role(name) is not None:
            return ()
        held = {
            role
            for connection, roles in self.bindings.items()
            if waiter_owner(connection) == name
            for role in roles
        }
        return tuple(sorted(held))


def _collect(events: Iterable[StoredEvent]) -> _Snapshot:
    """Read chats, watermarks and per-name activity from journal events."""
    snapshot = _Snapshot()
    for event in events:
        sender = str(event.payload.get("sender") or "").strip()
        if sender:
            snapshot.touch(waiter_owner(sender), event.ts)
        if event.kind == EventKind.CHAT:
            snapshot.add_chat(event)
        elif event.kind == EventKind.MAILBOX_WATERMARK:
            snapshot.add_watermark(event)
        elif event.kind == EventKind.ROLE_BINDING:
            snapshot.add_binding(event)
    return snapshot


def _glob_reaches(part: str, name: str) -> bool:
    """Return whether a glob part reaches a mailbox: a role key by its role, else the name."""
    role = mailbox_role(name)
    if role is not None:
        return addresses_role(part, role)
    return is_recipient(part, name)


def _recipients(target: str, names: dict[str, None], by_address: dict[str, list[str]]) -> set[str]:
    """Return the known mailboxes a directed target addresses."""
    recipients: set[str] = set()
    for part in (raw.strip() for raw in target.split(",")):
        if not part:
            continue
        if any(marker in part for marker in _GLOB_MARKERS):
            recipients.update(name for name in names if _glob_reaches(part, name))
        else:
            recipients.update(by_address.get(part, ()))
    return recipients


def _addresses(name: str) -> tuple[str, ...]:
    """Return the exact target parts that reach a mailbox."""
    role = mailbox_role(name)
    if role is not None:
        return (role,)
    return tuple(dict.fromkeys((name, name.split("/", 1)[0])))


def _mailboxes(snapshot: _Snapshot) -> dict[str, None]:
    """Return the snapshot's mailboxes, without plain names that are known roles."""
    roles = {role for role in map(mailbox_role, snapshot.names) if role is not None}
    return {name: None for name in snapshot.names if name not in roles}


def _watermark(snapshot: _Snapshot, name: str) -> int:
    """Return a mailbox's watermark; a role mailbox starts where its plain role name stood."""
    own = snapshot.watermarks.get(name, 0)
    role = mailbox_role(name)
    return own if role is None else max(own, snapshot.watermarks.get(role, 0))


def _pending_times(snapshot: _Snapshot, names: dict[str, None]) -> dict[str, list[float]]:
    """Return, per mailbox, the timestamps of the chats still pending in it."""
    by_address: dict[str, list[str]] = {}
    for name in names:
        for address in _addresses(name):
            by_address.setdefault(address, []).append(name)
    pending: dict[str, list[float]] = {name: [] for name in names}
    for chat in snapshot.chats:
        sender = str(chat.payload.get("sender") or "")
        for name in _recipients(str(chat.payload.get("target")), names, by_address):
            if chat.seq > _watermark(snapshot, name) and sender != name:
                pending[name].append(chat.ts)
    return pending


def mailbox_standings(events: Iterable[StoredEvent]) -> list[MailboxStanding]:
    """Compute the standing of every mailbox name one journal snapshot mentions.

    The names are those a directed chat lists exactly and those with a
    watermark, the same set the hub's pending projection remembers. A chat is
    pending for a name when it is directed, not on a channel, not sent by that
    name, addresses the name (a glob or bare project target included), and lies
    after the name's watermark. A role mailbox ``role:P/R`` counts the chats whose
    target reaches the role ``P/R``; a plain name equal to such a role is not
    listed. A watermark never counts beyond its own event.
    """
    snapshot = _collect(events)
    names = _mailboxes(snapshot)
    pending = _pending_times(snapshot, names)
    standings = [
        MailboxStanding(
            identity=name,
            project=project_of(name),
            pending=len(pending[name]),
            watermark=_watermark(snapshot, name),
            oldest_pending_ts=min(pending[name], default=None),
            last_activity_ts=snapshot.activity.get(name),
            roles=snapshot.roles_of(name),
        )
        for name in names
    ]
    return sorted(standings, key=lambda item: (item.project, item.identity))


def orphaned_mailboxes(
    standings: Iterable[MailboxStanding],
    *,
    now: float,
    dormant_after_days: float,
    active: Iterable[str] = (),
    project: str | None = None,
) -> list[MailboxStanding]:
    """Return mailboxes with pending mail whose name has been quiet too long.

    Parameters
    ----------
    standings : Iterable[MailboxStanding]
        Output of :func:`mailbox_standings`.
    now : float
        The reference wall-clock time.
    dormant_after_days : float
        A name counts as dormant after this many days without activity; a name
        that never acted is dormant. Must be positive.
    active : Iterable[str], optional
        Names connected now (from a live roster); a sidecar counts for its owner.
        These are never orphaned, whatever the journal says.
    project : str or None, optional
        Keep only this project's names.

    Returns
    -------
    list[MailboxStanding]
        Orphans, most pending mail first, then by name.
    """
    if not dormant_after_days > 0:
        raise ValueError("dormant_after_days must be positive")
    live = {waiter_owner(name) for name in active}
    threshold = dormant_after_days * _DAY
    orphans = []
    for standing in standings:
        if standing.pending == 0 or standing.identity in live:
            continue
        if project is not None and standing.project != project:
            continue
        quiet = standing.dormant_for(now)
        if quiet is None or quiet >= threshold:
            orphans.append(standing)
    return sorted(orphans, key=lambda item: (-item.pending, item.identity))
