# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — feed pages and acknowledgements on the authenticated connection
"""Answer ``history_request.feed_query``, ``feed_ack`` and ``feed_bind``.

Both run on the consumer's own authenticated connection, behind the ``feed-read``
grant (:data:`~synapse_channel.core.acl.FEED_READ`). The consumer they name is the
connection itself or one of its roles. See :mod:`synapse_channel.core.feed`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from synapse_channel.core.feed import (
    FeedRefused,
    bind_legacy_position,
    parse_feed_bind,
    parse_feed_query,
    read_page,
)
from synapse_channel.core.journal import EventKind, record_feed_ack, record_feed_bind
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_access import feed_ack_access, feed_bind_access
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.feed import FeedLedger
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.persistence import EventStore

    class FeedContext(HandlerContext, Protocol):
        """Capabilities consumed by the feed handlers."""

        feed_ledger: FeedLedger
        """What this hub served each consumer, and the acks its journal holds."""

        @property
        def stable_delivery_hub_id(self) -> str | None:
            """Return the operator's configured hub id, or ``None`` for a per-process one."""
            ...

        def roles_of(self, name: str) -> tuple[str, ...]:
            """Return the roles ``name`` currently answers to (empty tuple if none)."""
            ...


def _correlation(data: dict[str, Any]) -> dict[str, str]:
    """Return the request's correlation id when it is a bounded string."""
    request_id = data.get("request_id")
    return (
        {"request_id": request_id}
        if isinstance(request_id, str) and 0 < len(request_id) <= 128
        else {}
    )


def _owned_journal(hub: FeedContext, sender: str, consumer: str) -> EventStore:
    """Return the hub's journal once ``consumer`` is this connection or one of its roles.

    A cursor names the hub, so the feed needs the same durable, stable hub as
    delivery version 3: a journal and an operator-configured hub id. A hub without
    one draws a new id at every start, and every cursor would die with it.

    Raises
    ------
    FeedRefused
        When the consumer belongs to another connection, or the hub keeps no
        journal or has no configured hub id.
    """
    if consumer != sender and consumer not in hub.roles_of(sender):
        raise FeedRefused("feed consumer is not this connection or one of its roles")
    journal = hub.journal
    if journal is None or not hub.stable_delivery_hub_id:
        raise FeedRefused("the feed requires a durable journal and a configured hub id")
    return journal


async def handle_feed_query(
    hub: FeedContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one ``history_request.feed_query`` with a bounded page or a refusal.

    Parameters
    ----------
    hub : FeedContext
        The hub whose own journal is read; a consumer never opens it.
    sender : str
        Connection identity already admitted by the identity gates and the
        ``feed-read`` grant for every requested kind.
    data : dict[str, Any]
        The ``history_request`` frame holding ``feed_query``.
    websocket : object
        Requesting transport; the page is never broadcast.

    Notes
    -----
    A cursor of another hub is refused. A cursor of another journal instance is
    answered ``source_changed`` with the new instance and its floor, and no rows:
    the consumer rebinds deliberately, it never continues by itself. A cursor
    above the journal's highest sequence is refused, so a journal rolled back to
    an older copy fails closed until its sequence passes the old cursor.
    """
    page: dict[str, Any] = {"version": 1, "available": False, "hub_id": hub.hub_id, "rows": []}
    try:
        query = parse_feed_query(data.get("feed_query"))
        page.update(consumer=query.consumer, after_seq=query.after_seq, next_after=query.after_seq)
        journal = _owned_journal(hub, sender, query.consumer)
        instance = journal.journal_instance()
        max_seq = journal.max_seq()
        page.update(
            journal_instance=instance.instance if instance else "",
            instance_floor=instance.floor if instance else 0,
            max_seq=max_seq,
        )
        if query.hub_id and query.hub_id != hub.hub_id:
            raise FeedRefused("feed cursor belongs to a different hub")
        if instance is None:
            raise FeedRefused("the journal records no instance; a feed cursor cannot be bound")
        if query.journal_instance and query.journal_instance != instance.instance:
            page["source_changed"] = {
                "journal_instance": instance.instance,
                "instance_floor": instance.floor,
            }
            raise FeedRefused("feed source changed: the journal is a different instance")
        if query.after_seq > max_seq:
            raise FeedRefused("feed cursor exceeds the retained journal")
        result = read_page(journal, query, hub.feed_ledger.markers)
        hub.feed_ledger.served(query.consumer, instance.instance, result.next_after)
        page.update(
            available=True,
            rows=list(result.rows),
            next_after=result.next_after,
            has_more=result.has_more,
        )
    except FeedRefused as exc:
        page["error"] = str(exc)
    await hub.send_json(
        websocket,
        hub.system(
            "Feed page",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            **_correlation(data),
            feed_page=page,
        ),
    )


def _ack_fields(data: dict[str, Any]) -> tuple[str, str, str, int]:
    """Return ``(consumer, hub_id, journal_instance, acked_seq)`` of a valid ack frame."""
    consumer, hub_id, instance, acked = (
        data.get("consumer"),
        data.get("hub_id"),
        data.get("journal_instance"),
        data.get("acked_seq"),
    )
    if (
        not isinstance(consumer, str)
        or not consumer.strip()
        or not isinstance(hub_id, str)
        or not isinstance(instance, str)
        or type(acked) is not int
        or not 0 <= acked <= 9223372036854775807
    ):
        raise FeedRefused("invalid feed acknowledgement")
    return consumer, hub_id, instance, acked


async def handle_feed_ack(
    hub: FeedContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Record a consumer's highest ingested sequence, durably and never backwards.

    An ack at or below the kept one is a no-op. An ack above the highest page this
    hub served the consumer is refused, so a consumer cannot acknowledge rows it
    was not given. A higher ack is journalled as a ``feed_ack`` event first and
    kept afterwards.

    Parameters
    ----------
    hub : FeedContext
        The hub whose journal holds the acks.
    sender : str
        Connection identity already admitted with ``feed-read`` on ``feed:ack``.
    data : dict[str, Any]
        The ``feed_ack`` frame.
    websocket : object
        Requesting transport; the reply is never broadcast.
    """
    result: dict[str, Any] = {"version": 1, "recorded": False}
    try:
        consumer, hub_id, instance_id, acked = _ack_fields(data)
        result.update(consumer=consumer, journal_instance=instance_id)
        journal = _owned_journal(hub, sender, consumer)
        instance = journal.journal_instance()
        if hub_id != hub.hub_id:
            raise FeedRefused("feed acknowledgement names a different hub")
        if instance is None or instance_id != instance.instance:
            raise FeedRefused("feed acknowledgement names a different journal instance")
        if acked > hub.feed_ledger.served_through(consumer, instance_id):
            raise FeedRefused("feed acknowledgement is ahead of the pages served to it")
        kept = hub.feed_ledger.acked(journal, consumer, instance_id)
        if acked > kept:
            record_feed_ack(
                journal,
                consumer=consumer,
                hub_id=hub_id,
                journal_instance=instance_id,
                acked_seq=acked,
            )
            hub.feed_ledger.record(consumer, instance_id, acked)
            kept = acked
            result["recorded"] = True
        result["acked_seq"] = kept
    except FeedRefused as exc:
        result["error"] = str(exc)
    await hub.send_json(
        websocket,
        hub.system(
            "Feed acknowledgement",
            msg_type=MessageType.FEED_ACK_RESULT,
            target=sender,
            **_correlation(data),
            feed_ack=result,
        ),
    )


async def handle_feed_bind(
    hub: FeedContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Bind a consumer's legacy, sequence-only position to a cursor, by proof.

    It is a one-time migration act. A consumer that already holds an ack in this
    journal instance has a feed position and is refused. One attempt per consumer
    and instance is admitted per :data:`~synapse_channel.core.feed.FEED_BIND_INTERVAL_SECONDS`.
    A verified binding is journalled as a ``feed_bind`` event before the answer;
    a refusal writes nothing.

    Parameters
    ----------
    hub : FeedContext
        The hub whose journal holds the proof row.
    sender : str
        Connection identity already admitted with ``feed-read`` on the proof's kind.
    data : dict[str, Any]
        The ``feed_bind`` frame.
    websocket : object
        Requesting transport; the reply is never broadcast.
    """
    result: dict[str, Any] = {"version": 1, "bound": False}
    try:
        binding = parse_feed_bind(data)
        result.update(consumer=binding.consumer)
        journal = _owned_journal(hub, sender, binding.consumer)
        instance = journal.journal_instance()
        result.update(hub_id=hub.hub_id, journal_instance=instance.instance if instance else "")
        if binding.hub_id and binding.hub_id != hub.hub_id:
            raise FeedRefused("feed binding names a different hub")
        if instance is None:
            raise FeedRefused("the journal records no instance; a feed cursor cannot be bound")
        if hub.feed_ledger.acked(journal, binding.consumer, instance.instance) > 0:
            raise FeedRefused(
                "the consumer already has a feed position in this journal instance",
                "already_bound",
            )
        if not hub.feed_ledger.admit_bind(binding.consumer, instance.instance, time.monotonic()):
            raise FeedRefused("one feed binding attempt per consumer per minute", "rate_limited")
        after_seq = bind_legacy_position(journal, binding, hub.feed_ledger.markers)
        record_feed_bind(
            journal,
            consumer=binding.consumer,
            hub_id=hub.hub_id,
            journal_instance=instance.instance,
            after_seq=after_seq,
            proof_kind=binding.kind,
            proof_seq=binding.seq,
            payload_sha256=binding.payload_sha256,
        )
        result.update(bound=True, after_seq=after_seq)
    except FeedRefused as exc:
        result["error"] = str(exc)
        if exc.code:
            result["code"] = exc.code
    await hub.send_json(
        websocket,
        hub.system(
            "Feed binding",
            msg_type=MessageType.FEED_BIND_RESULT,
            target=sender,
            **_correlation(data),
            feed_bind=result,
        ),
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.FEED_ACK,),
        handler=handle_feed_ack,
        reply_types=(MessageType.FEED_ACK_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=feed_ack_access,
        event_kinds=(EventKind.FEED_ACK,),
        minimum_wire_version=7,
        commands=("feed ack",),
    ),
    VerbSpec(
        request_types=(MessageType.FEED_BIND,),
        handler=handle_feed_bind,
        reply_types=(MessageType.FEED_BIND_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=feed_bind_access,
        event_kinds=(EventKind.FEED_BIND,),
        minimum_wire_version=7,
        commands=("feed bind",),
    ),
)
"""The feed acknowledgement and binding verbs; the page is served by ``history_request``."""
