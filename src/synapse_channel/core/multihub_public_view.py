# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the public view of a journal event that multi-hub followers see
"""The public view of a journal event that multi-hub followers see.

A hub serves its journal to authorised followers over the multi-hub log. Its own journal
keeps private accounting that no other hub needs: every claim-snapshot event
(:data:`~synapse_channel.core.replay.SNAPSHOT_KINDS`: ``claim``, ``task_update``,
``checkpoint`` and ``handoff``) carries
:meth:`~synapse_channel.core.state_models.TaskClaim.as_persisted_dict`, which adds the
credential-derived ``quota_principal`` and the forwarding ``via_peer`` to the public
:meth:`~synapse_channel.core.state_models.TaskClaim.as_dict` fields, and other kinds carry
principals, session identifiers, token identifiers and request keys.

:func:`public_event_view` is the one rule for what a follower sees (view
:data:`PUBLIC_VIEW_VERSION`). It denies by default, by kind:

* a kind with a named follower consumer is **served**, keeping only the fields its consumers
  read (an allow-list per kind), so a field added later stays private until it is listed;
* the operation records that followers read lose only ``request_key``;
* ``ledger_task`` is served unchanged, because a content-bound causal parent references it by
  its complete-event fingerprint;
* every other kind is **withheld**: it keeps its ``seq``, ``ts`` and ``kind``, and its payload
  becomes the constant stub ``{"_withheld": PUBLIC_VIEW_VERSION}``. The follower validates a
  contiguous log, so a withheld event is stubbed, never dropped.

The view is applied at both ends of the multi-hub log: the owning hub serves it
(``handlers.multihub._read_snapshot``), and a follower applies it to every fetched event
before it fingerprints or folds them
(:meth:`~synapse_channel.core.multihub_follower.MultiHubFollower.poll`). It is idempotent,
so a raw event from an older owner and the served event from a newer owner give the same
fingerprint, and an upgrade raises no false equivocation. A new kind is withheld until it is
listed here with its consumer and its fields, in a reviewed change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS

__all__ = [
    "PUBLIC_CLAIM_FIELDS",
    "PUBLIC_VIEW_VERSION",
    "SERVED_KINDS",
    "is_withheld",
    "public_event_view",
]

PUBLIC_VIEW_VERSION = "public-v2"
"""The version of the view, recorded in every withheld event's stub."""

_WITHHELD_FIELD = "_withheld"

PUBLIC_CLAIM_FIELDS: frozenset[str] = frozenset(
    {
        "checkpoint",
        "claimed_at",
        "data_ref",
        "epoch",
        "git",
        "lease_expires_at",
        "note",
        "owner",
        "ownership_epoch",
        "path_identity",
        "paths",
        "status",
        "task_id",
        "version",
        "worktree",
    }
)
"""The fields of :meth:`~synapse_channel.core.state_models.TaskClaim.as_dict`, the public claim."""

_RECEIPT_FIELDS = frozenset({"delivered", "message_seq"})

_FIELD_LISTS: Mapping[str, frozenset[str]] = {
    **dict.fromkeys(SNAPSHOT_KINDS, PUBLIC_CLAIM_FIELDS),
    # The follower fold and Fleet's incident timeline read a release as a claim snapshot
    # plus how it was released.
    EventKind.RELEASE: PUBLIC_CLAIM_FIELDS | {"via"},
    # The follower fold's observed progress.
    EventKind.LEDGER_PROGRESS: frozenset({"author", "kind", "posted_at", "task_id", "text"}),
    # Fleet's pool adverts and journal checkpoints verify a signed advert.
    EventKind.ENTITLEMENT_ADVERT: frozenset(
        {"advert", "advertiser", "advertiser_key_id", "hub_id"}
    ),
    # Fleet's health view of partitions and heals.
    EventKind.MULTIHUB_PARTITION: frozenset(
        {
            "contesting_hubs",
            "local_hub_id",
            "namespace",
            "outcome",
            "owner_hub_id",
            "transition",
        }
    ),
    EventKind.MULTIHUB_HEAL: frozenset(
        {
            "local_hub_id",
            "namespace",
            "observation_refreshed",
            "outcome",
            "owner_hub_id",
            "previous_contesting_hubs",
        }
    ),
    # Fleet's ops mirror reads a receipt's state and the message it belongs to.
    EventKind.DELIVERY_RECEIPT_IMMEDIATE: _RECEIPT_FIELDS,
    EventKind.DELIVERY_RECEIPT_DEFERRED: _RECEIPT_FIELDS,
    EventKind.DELIVERY_RECEIPT_EXPIRED: _RECEIPT_FIELDS,
    EventKind.DELIVERY_RECEIPT_REQUESTED: _RECEIPT_FIELDS,
}

_OPERATION_KINDS: frozenset[str] = frozenset(
    {
        # Fleet's operation evidence and its scope-stop reading.
        EventKind.OPERATION_DECLARED,
        EventKind.OPERATION_OUTCOME,
        EventKind.OPERATION_VERIFIED,
        EventKind.OPERATION_ATTEMPT_ANSWERED,
        EventKind.OPERATION_SEALED,
        EventKind.OPERATION_STOP_READBACK,
        EventKind.OPERATION_STOPPED,
        EventKind.OPERATION_RESUMED,
    }
)
_OPERATION_PRIVATE_FIELDS = frozenset({"request_key"})

_WHOLE_KINDS: frozenset[str] = frozenset({EventKind.LEDGER_TASK})

SERVED_KINDS: frozenset[str] = frozenset(_FIELD_LISTS) | _OPERATION_KINDS | _WHOLE_KINDS
"""The kinds a follower receives with a payload; every other kind is withheld."""


def _withheld_payload() -> dict[str, Any]:
    """Return a fresh copy of the constant stub of a withheld event."""
    return {_WITHHELD_FIELD: PUBLIC_VIEW_VERSION}


def is_withheld(event: StoredEvent) -> bool:
    """Return whether ``event`` is the stub of a withheld event.

    Parameters
    ----------
    event : StoredEvent
        One event as a follower received it.

    Returns
    -------
    bool
        ``True`` when the payload is exactly the withheld stub of this view version.
    """
    return event.payload == _withheld_payload()


def public_event_view(event: StoredEvent) -> StoredEvent:
    """Return ``event`` as a multi-hub follower may see it.

    Parameters
    ----------
    event : StoredEvent
        One event read from a hub's journal, raw or already viewed.

    Returns
    -------
    StoredEvent
        The event with only its kind's listed fields, without ``request_key`` for an
        operation record, unchanged for ``ledger_task``, or as the withheld stub for a kind
        no follower consumes. The same object when nothing changes.
    """
    if event.kind in _WHOLE_KINDS:
        return event
    fields = _FIELD_LISTS.get(event.kind)
    if fields is not None:
        payload = {key: value for key, value in event.payload.items() if key in fields}
    elif event.kind in _OPERATION_KINDS:
        payload = {
            key: value
            for key, value in event.payload.items()
            if key not in _OPERATION_PRIVATE_FIELDS
        }
    else:
        payload = _withheld_payload()
    if payload == event.payload:
        return event
    return StoredEvent(seq=event.seq, ts=event.ts, kind=event.kind, payload=payload)
