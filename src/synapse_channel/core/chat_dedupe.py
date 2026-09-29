# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-side chat retry dedupe on (sender, client_msg_id)
"""Accept a retried chat once: the hub remembers ``(sender, client_msg_id)``.

A client that loses its connection before it knows its chat got through cannot
tell whether the hub accepted it, so it sends again. Before K4-WF8 the hub stored,
journalled and delivered every copy, and receivers had to deduplicate. Now a chat
that carries a ``client_msg_id`` and reached a live recipient is remembered with a
digest of what it says; a later chat from the same sender with the same id is:

* a **duplicate** when its content digest matches — it is not appended, journalled
  or delivered again, and the sender gets a ``system`` notice marked ``duplicate``
  that names the first copy's ``msg_id`` (and journal ``seq``). It is not a chat
  frame, because clients drop chat frames that carry their own name;
* a **conflict** when the content differs — it is refused, because the id was
  already spent on a different message (the forward ledger refuses a reused
  forward id the same way).

A chat that reached nobody is not remembered, so resending it is a redelivery
attempt and is routed again. Retention is bounded by entry count and age and lives
in the hub process: the journal does not record whether a copy was received, so it
cannot safely re-seed the memory after a restart. Chats without a
``client_msg_id`` keep the at-least-once behaviour.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from synapse_channel.core.agent_liveness import waiter_owner

DEFAULT_CHAT_DEDUPE_CAPACITY = 4096
"""Most ``(sender, client_msg_id)`` pairs remembered at once."""

DEFAULT_CHAT_DEDUPE_WINDOW = 86_400.0
"""Seconds a remembered chat still answers a retry (the forward-retry horizon)."""

_RECALLED_FIELDS = ("msg_id", "seq", "channel", "forward_id")
"""Fields of the accepted frame a duplicate notice repeats; the body is not kept."""

_VOLATILE_FIELDS = frozenset(
    {
        "type",
        "sender",
        "timestamp",
        "client_timestamp",
        "msg_id",
        "seq",
        "hub_id",
        "idem_key",
        "auth",
        "signature",
        "token",
        "duplicate",
        "forward_id",
    }
)


def chat_digest(frame: Mapping[str, Any]) -> str:
    """Return the digest of what a chat says, ignoring transport and hub stamps.

    Parameters
    ----------
    frame : Mapping[str, Any]
        A chat frame, before or after the hub stamped it.

    Returns
    -------
    str
        Hex SHA-256 of the canonical JSON of the content fields.
    """
    content = {key: value for key, value in frame.items() if key not in _VOLATILE_FIELDS}
    # The hub rewrites a ``-rx`` transport alias to its owner before journalling, so
    # the digest compares the owner either way.
    content["target"] = waiter_owner(str(frame.get("target") or "all"))
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChatDedupeVerdict:
    """What to do with a chat that carries a ``client_msg_id``.

    Attributes
    ----------
    outcome : {"new", "duplicate", "conflict"}
        ``new`` routes normally; ``duplicate`` answers with ``original``; ``conflict``
        refuses.
    original : dict or None
        For a duplicate, the identifying fields of the frame accepted the first time
        (``msg_id`` and, when present, ``seq``, ``channel``, ``forward_id``).
    """

    outcome: Literal["new", "duplicate", "conflict"]
    original: dict[str, Any] | None = None


@dataclass
class _Entry:
    digest: str
    accepted_at: float
    frame: dict[str, Any]


class ChatDedupe:
    """Bounded memory of accepted chats keyed by ``(sender, client_msg_id)``.

    Parameters
    ----------
    capacity : int, optional
        Most pairs remembered; the oldest is forgotten first.
    window_seconds : float, optional
        Age after which a remembered chat no longer answers a retry.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CHAT_DEDUPE_CAPACITY,
        window_seconds: float = DEFAULT_CHAT_DEDUPE_WINDOW,
    ) -> None:
        if capacity < 1:
            raise ValueError("chat dedupe capacity must be at least 1")
        if not window_seconds > 0.0:
            raise ValueError("chat dedupe window must be a positive number of seconds")
        self.capacity = capacity
        self.window_seconds = window_seconds
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()

    def __len__(self) -> int:
        """Return how many ``(sender, client_msg_id)`` pairs are remembered."""
        return len(self._entries)

    def check(
        self, sender: str, client_msg_id: str, digest: str, *, now: float
    ) -> ChatDedupeVerdict:
        """Classify an incoming chat against what this sender already sent.

        Parameters
        ----------
        sender : str
            The hub-resolved sender.
        client_msg_id : str
            The normalised client id (non-empty).
        digest : str
            :func:`chat_digest` of the incoming frame, taken before routing stamps it.
        now : float
            Wall-clock time, compared with each entry's acceptance time.
        """
        key = (sender, client_msg_id)
        entry = self._entries.get(key)
        if entry is None or now - entry.accepted_at > self.window_seconds:
            return ChatDedupeVerdict("new")
        if entry.digest != digest:
            return ChatDedupeVerdict("conflict")
        return ChatDedupeVerdict("duplicate", dict(entry.frame))

    def remember(
        self,
        sender: str,
        client_msg_id: str,
        digest: str,
        frame: Mapping[str, Any],
        *,
        accepted_at: float,
    ) -> None:
        """Record an accepted chat, evicting the oldest pair beyond capacity.

        ``digest`` is the incoming frame's digest; ``frame`` is the accepted frame, whose
        identifying fields a later duplicate notice repeats.
        """
        key = (sender, client_msg_id)
        recalled = {field: frame[field] for field in _RECALLED_FIELDS if field in frame}
        self._entries.pop(key, None)
        self._entries[key] = _Entry(digest, accepted_at, recalled)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
