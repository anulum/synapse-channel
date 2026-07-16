# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-most-once, finding-quota, and message-id bookkeeping
"""Idempotency, durable-finding quota, and message-id bookkeeping for the hub.

:class:`HubLedgerGuard` owns the hub's three "ledger" concerns: the strictly
increasing per-hub message id, the at-most-once idempotency cache (so a retried
mutation replays its original response instead of re-applying), and the per-agent
durable-finding quota. It is seeded from a durable-log replay on construction so the
guarantees survive a restart, and it journals each remembered response when a log is
attached. The cache and a duplicate-replay helper are exposed so the hub keeps only
thin wrappers over the handler-facing names.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.idempotency import IdempotencyCache
from synapse_channel.core.journal import record_idempotency
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType

if TYPE_CHECKING:
    from synapse_channel.core.persistence import EventStore


_MUTATING_TYPES = (
    frozenset(
        {
            MessageType.CLAIM,
            MessageType.RELEASE,
            MessageType.TASK_UPDATE,
            MessageType.HANDOFF,
            MessageType.CHECKPOINT,
        }
    )
    | RESOURCE_TYPE_ALIASES
)
"""Inbound message types eligible for idempotent replay protection."""


class HubLedgerGuard:
    """Own the hub's message-id, idempotency cache, and finding quota.

    Parameters
    ----------
    max_findings_per_agent : int
        The durable-finding ceiling a single agent may admit before rejection.
    journal : EventStore or None
        When set, each remembered response is appended to the durable log so the
        at-most-once guarantee survives a restart.
    message_seq : int, optional
        The message sequence to resume from (a replay's high-water mark, or ``0``).
    finding_counts : Mapping[str, int] or None, optional
        Per-agent finding tallies to resume from after a replay.
    idempotency_seed : Iterable[tuple[str, dict]], optional
        Reconstructed idempotency entries, oldest first, to seed the bounded cache.
    """

    def __init__(
        self,
        *,
        max_findings_per_agent: int,
        journal: EventStore | None,
        message_seq: int = 0,
        finding_counts: Mapping[str, int] | None = None,
        idempotency_seed: Iterable[tuple[str, dict[str, Any]]] = (),
    ) -> None:
        self._max_findings_per_agent = max_findings_per_agent
        self._journal = journal
        self._message_seq = message_seq
        self._findings_by_agent: dict[str, int] = dict(finding_counts or {})
        self._cache = IdempotencyCache()
        for key, response in idempotency_seed:
            self._cache.put(key, response)

    @property
    def idempotency(self) -> IdempotencyCache:
        """Return the live at-most-once cache (so the hub can alias it)."""
        return self._cache

    @property
    def message_seq(self) -> int:
        """Return the current message sequence high-water mark."""
        return self._message_seq

    def next_msg_id(self) -> int:
        """Return a strictly increasing per-hub message sequence number."""
        self._message_seq += 1
        return self._message_seq

    @staticmethod
    def idempotency_key(data: dict[str, Any]) -> str:
        """Return a sender/type-namespaced idempotency key, or an empty string.

        The client-supplied ``idem_key`` alone is not safe to key the cache on: a
        malicious or merely buggy agent that reuses another agent's key would have
        its mutation silently suppressed and be answered with a replay of the
        first agent's response — cross-agent mutation suppression plus a grant-data
        leak. Namespace the raw key by the (already authorised) sender and the
        message type, so one agent's key can never collide with another's, and a
        ``claim`` can never collide with a ``release`` that reuses the same key.
        The ``NUL`` separator cannot appear in a normal identity or type token, so
        the three segments never run together. Empty when no key was supplied, so
        an unkeyed mutation is never deduplicated.

        The replay guard runs only after the sender has been authorised
        (``hub.py`` authorises and resolves the identity before checking for a
        duplicate), so ``data["sender"]`` here is the bound identity, not a raw
        unverified claim.
        """
        raw = str(data.get("idem_key") or "")
        if not raw:
            return ""
        sender = str(data.get("sender") or "").strip()
        msg_type = str(data.get("type") or "").strip().lower()
        return f"{sender}\x00{msg_type}\x00{raw}"

    def remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache the response of an applied mutation under its idempotency key.

        The cache is also journalled when a durable log is attached, so the
        at-most-once guarantee survives a hub restart (a retried mutation replays
        the original response rather than re-applying).
        """
        key = self.idempotency_key(data)
        if key:
            self._cache.put(key, response)
            if self._journal is not None:
                record_idempotency(self._journal, key, response)

    def reserve_finding_slot(self, agent: str) -> tuple[bool, str]:
        """Reserve one durable-finding quota slot for ``agent``.

        Parameters
        ----------
        agent : str
            Hub-authenticated agent name that authored the finding.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` when the slot was reserved, otherwise
            ``(False, reason)`` when the agent already reached the configured
            durable-finding quota.
        """
        owner = agent.strip()
        admitted = self._findings_by_agent.get(owner, 0)
        if admitted >= self._max_findings_per_agent:
            return (
                False,
                f"Agent '{owner}' has reached the {self._max_findings_per_agent} "
                "durable-finding quota.",
            )
        self._findings_by_agent[owner] = admitted + 1
        return True, f"Agent '{owner}' finding admitted."

    async def maybe_replay_duplicate(
        self,
        msg_type: str,
        data: dict[str, Any],
        websocket: Any,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Parameters
        ----------
        msg_type : str
            The inbound message type.
        data : dict[str, Any]
            The decoded message.
        websocket : Any
            The sender's socket.
        send_json : Callable
            The hub's per-socket send, used to re-send the original response.

        Returns
        -------
        bool
            ``True`` when the message was a recognised duplicate of an already
            applied mutation and its original response was re-sent to the sender;
            ``False`` when the message should be processed normally.
        """
        if msg_type not in _MUTATING_TYPES:
            return False
        key = self.idempotency_key(data)
        if not key:
            return False
        cached = self._cache.get(key)
        if cached is None:
            return False
        await send_json(websocket, cached)
        return True
