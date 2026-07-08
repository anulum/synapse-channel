# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private-channel membership registry
"""Audience-scoped private-channel membership for the routing hub.

A private channel is a named recipient set: a message addressed to a channel is
delivered only to that channel's joined, online members instead of broadcast to
every connected socket. This is audience scoping inside a trusted local hub, not
cryptographic secrecy by itself — the hub still sees channel ids, members, and
metadata, and any member can copy plaintext. Client-side encrypted payload
envelopes in :mod:`synapse_channel.core.payload_crypto` can hide selected bodies
from the hub while preserving this routing metadata. Who-may-join authorization
is the future identity/ACL concern. This first tranche keeps join open (any
agent may join a channel by id) so teams can route operational chatter cleanly;
membership is explicit, so a non-member never receives a channel message.

See :doc:`../../docs/private-channels` for the full design and the deferred
projection work (per-channel history policy, retention boundaries, event-query
and relay filtering).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.numeric_coercion import safe_int

MAX_CHANNEL_ID_LENGTH = 200
"""Largest accepted channel id, bounding registry keys."""

MAX_CHANNELS = 4096
"""Largest number of distinct channels retained, bounding hub memory."""


@dataclass
class Channel:
    """One private channel: an owner, a label, and an explicit member set.

    Parameters
    ----------
    channel_id : str
        Stable, inspectable channel identifier carried in protocol payloads.
    owner : str
        Agent name that created the channel; always a member.
    label : str
        Human-readable display label.
    members : set[str]
        Agent names currently joined to the channel.
    history : list[dict[str, Any]]
        Bounded live chat history visible only to current members.
    """

    channel_id: str
    owner: str
    label: str
    members: set[str] = field(default_factory=set)
    history: list[dict[str, Any]] = field(default_factory=list)


class ChannelRegistry:
    """In-memory registry of private channels and their members.

    The registry is deliberately non-durable for this tranche: channels live for
    the hub process. Routing reads :meth:`members` to scope delivery; the hub
    never delivers a channel message to a non-member.
    """

    def __init__(self, *, max_channels: int = MAX_CHANNELS) -> None:
        self.max_channels = safe_int(max_channels, default=MAX_CHANNELS, min_value=1)
        self._channels: dict[str, Channel] = {}

    @staticmethod
    def _normalise_id(channel_id: str) -> str:
        """Return the trimmed channel id, or ``""`` when it is unusable."""
        cid = str(channel_id or "").strip()
        if not cid or len(cid) > MAX_CHANNEL_ID_LENGTH:
            return ""
        return cid

    def exists(self, channel_id: str) -> bool:
        """Return whether a channel with ``channel_id`` exists."""
        return self._normalise_id(channel_id) in self._channels

    def create(self, channel_id: str, owner: str, label: str = "") -> tuple[bool, str]:
        """Create a channel owned by ``owner`` (its first member).

        Parameters
        ----------
        channel_id : str
            Requested channel id; trimmed and length-bounded.
        owner : str
            Creating agent name, added as the first member.
        label : str, optional
            Display label; defaults to the channel id.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` on creation, ``(False, reason)`` otherwise.
        """
        cid = self._normalise_id(channel_id)
        if not cid:
            return False, "invalid channel id"
        owner_name = str(owner or "").strip()
        if not owner_name:
            return False, "invalid owner"
        if cid in self._channels:
            return False, f"channel '{cid}' already exists"
        if len(self._channels) >= self.max_channels:
            return False, "channel registry is full"
        self._channels[cid] = Channel(
            channel_id=cid, owner=owner_name, label=str(label).strip() or cid, members={owner_name}
        )
        return True, f"created channel '{cid}'"

    def join(self, channel_id: str, member: str) -> tuple[bool, str]:
        """Add ``member`` to a channel, returning whether it changed membership."""
        cid = self._normalise_id(channel_id)
        channel = self._channels.get(cid)
        if channel is None:
            return False, f"channel '{cid}' does not exist"
        member_name = str(member or "").strip()
        if not member_name:
            return False, "invalid member"
        if member_name in channel.members:
            return False, f"already a member of '{cid}'"
        channel.members.add(member_name)
        return True, f"joined '{cid}'"

    def leave(self, channel_id: str, member: str) -> tuple[bool, str]:
        """Remove ``member`` from a channel; drop the channel when it empties."""
        cid = self._normalise_id(channel_id)
        channel = self._channels.get(cid)
        if channel is None:
            return False, f"channel '{cid}' does not exist"
        member_name = str(member or "").strip()
        if member_name not in channel.members:
            return False, f"not a member of '{cid}'"
        channel.members.discard(member_name)
        if not channel.members:
            self._channels.pop(cid, None)
        return True, f"left '{cid}'"

    def is_member(self, channel_id: str, name: str) -> bool:
        """Return whether ``name`` is a joined member of the channel."""
        channel = self._channels.get(self._normalise_id(channel_id))
        if channel is None:
            return False
        return str(name or "").strip() in channel.members

    def members(self, channel_id: str) -> frozenset[str]:
        """Return the channel's current member names, empty when absent."""
        channel = self._channels.get(self._normalise_id(channel_id))
        if channel is None:
            return frozenset()
        return frozenset(channel.members)

    def owner(self, channel_id: str) -> str | None:
        """Return the channel owner name, or ``None`` when the channel is absent."""
        channel = self._channels.get(self._normalise_id(channel_id))
        return channel.owner if channel is not None else None

    def channels_for(self, name: str) -> list[str]:
        """Return the sorted channel ids ``name`` is a member of."""
        member_name = str(name or "").strip()
        return sorted(
            cid for cid, channel in self._channels.items() if member_name in channel.members
        )

    def retain_message(
        self,
        channel_id: str,
        message: dict[str, Any],
        *,
        max_messages: int,
    ) -> None:
        """Retain one live channel message behind the membership boundary.

        Parameters
        ----------
        channel_id : str
            Channel whose live history receives the message.
        message : dict[str, Any]
            Hub-stamped chat envelope to copy into the channel history.
        max_messages : int
            Maximum messages retained for this channel. Values below ``1`` keep
            the latest message only.
        """
        channel = self._channels.get(self._normalise_id(channel_id))
        if channel is None:
            return
        channel.history.append(dict(message))
        keep = safe_int(max_messages, default=1, min_value=1)
        if len(channel.history) > keep:
            del channel.history[: len(channel.history) - keep]

    def history_for(
        self, channel_id: str, member: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return bounded channel history visible to ``member``.

        Parameters
        ----------
        channel_id : str
            Channel id to read.
        member : str
            Requesting agent name; must be a current member.
        limit : int or None, optional
            Maximum number of most-recent messages to return. ``None`` returns
            the whole retained window.

        Returns
        -------
        list[dict[str, Any]]
            Copies of visible messages, or an empty list for non-members and
            unknown channels.
        """
        channel = self._channels.get(self._normalise_id(channel_id))
        if channel is None or str(member or "").strip() not in channel.members:
            return []
        keep = len(channel.history) if limit is None else safe_int(limit, default=0, min_value=0)
        selected = channel.history[-keep:] if keep else []
        return [dict(message) for message in selected]

    def snapshot(self) -> list[dict[str, object]]:
        """Return a JSON-friendly snapshot of channels and member counts."""
        return [
            {
                "channel_id": channel.channel_id,
                "label": channel.label,
                "owner": channel.owner,
                "members": sorted(channel.members),
                "history_size": len(channel.history),
            }
            for channel in sorted(self._channels.values(), key=lambda c: c.channel_id)
        ]
