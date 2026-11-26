# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the public view of journal events that multi-hub followers see
"""A follower sees only what its consumers read, and raw and viewed serving agree.

The owning hub's journal keeps ``quota_principal`` and ``via_peer`` on every claim-snapshot
event for its own replay. A follower must neither keep nor show them, and an owner upgraded to
serve the public view must not look, to a follower that read its raw rows first, like an owner
that changed its history. A ledger task, which a content-bound causal parent references by its
complete-event fingerprint, is never projected. Every kind no follower consumes is withheld as
a constant stub that keeps its sequence, time and kind, so the follower's log stays contiguous.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from synapse_channel.core.journal import EventKind, record_claim, record_task_update
from synapse_channel.core.multihub_equivocation import (
    event_fingerprint,
    validate_content_bound_batch,
)
from synapse_channel.core.multihub_follower import MultiHubFollower, store_fetcher
from synapse_channel.core.multihub_merge import HubEvent, tag_events
from synapse_channel.core.multihub_public_view import (
    PUBLIC_CLAIM_FIELDS,
    PUBLIC_VIEW_VERSION,
    SERVED_KINDS,
    is_withheld,
    public_event_view,
)
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS
from synapse_channel.core.state_models import GitContext, TaskClaim

_PEER = "syn-b"
_PRIVATE = frozenset({"quota_principal", "via_peer"})


def _claim() -> TaskClaim:
    """Return a forwarded claim with every optional public part and both private fields."""
    return TaskClaim(
        task_id="t1",
        owner="SYNAPSE-CHANNEL/alice",
        note="forwarded work",
        claimed_at=10.0,
        # A lease the journal's wall clock has not reached: a follower drops a claim once its
        # own hub's journal passes the claim's lease (multi-hub lease lapse).
        lease_expires_at=4_102_444_800.0,
        quota_principal="federation-peer:syn-c",
        paths=("a.py",),
        path_identity=ClaimScopeIdentity(
            worktree_path="/repo",
            worktree_object_id="811:10:7",
            case_sensitive=True,
            paths=(CanonicalPathIdentity("a.py", "a.py", "811:11:3"),),
        ),
        epoch=3,
        version=1,
        checkpoint="cp",
        git=GitContext(branch="feature"),
        ownership_epoch=2,
        via_peer="syn-c",
    )


def _store(path: Path) -> EventStore:
    """Return a journal holding one claim and one task update, each with private fields."""
    store = EventStore(path)
    record_claim(store, _claim())
    record_task_update(store, _claim())
    return store


def test_the_public_fields_are_exactly_the_public_claim_snapshot() -> None:
    """The allow-list is the field set of ``TaskClaim.as_dict``; the private pair is outside it."""
    claim = _claim()
    assert set(claim.as_dict()) == PUBLIC_CLAIM_FIELDS
    assert set(claim.as_persisted_dict()) == PUBLIC_CLAIM_FIELDS | _PRIVATE


def test_every_claim_snapshot_kind_keeps_only_its_public_fields() -> None:
    """Claim, task update, checkpoint and handoff lose the private fields and keep the rest."""
    assert {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF} == (
        SNAPSHOT_KINDS
    )
    for kind in SNAPSHOT_KINDS:
        raw = StoredEvent(seq=4, ts=12.5, kind=kind, payload=_claim().as_persisted_dict())
        view = public_event_view(raw)
        assert view.payload == _claim().as_dict()
        assert (view.seq, view.ts, view.kind) == (4, 12.5, kind)


def test_a_handoff_loses_the_hubs_own_notification_routing() -> None:
    """``_notification`` routes the owning hub's notice; a follower sees the public claim only."""
    payload = _claim().as_persisted_dict() | {"_notification": {"route": "handoffs", "to": "bob"}}
    raw = StoredEvent(seq=5, ts=13.0, kind=EventKind.HANDOFF, payload=payload)
    assert public_event_view(raw).payload == _claim().as_dict()


def test_a_content_bound_parent_keeps_one_fingerprint_across_raw_and_projected_serving(
    tmp_path: Path,
) -> None:
    """A ledger task is never projected, so a child's parent fingerprint verifies either way."""
    store = _store(tmp_path / "peer.db")
    store.append(EventKind.LEDGER_TASK, {"task_id": "t1", "via_peer": "syn-c", "title": "x"})
    raw = tuple(store.read_all())
    store.close()
    projected = tuple(public_event_view(event) for event in raw)
    raw_parent, projected_parent = (
        next(event for event in tag_events(_PEER, served) if event.kind == EventKind.LEDGER_TASK)
        for served in (raw, projected)
    )
    assert event_fingerprint(raw_parent) == event_fingerprint(projected_parent)
    staged: dict[tuple[str, int], HubEvent] = {}
    for served in (raw, projected):
        viewed = tag_events(_PEER, (public_event_view(event) for event in served))
        validate_content_bound_batch(staged, viewed, peer_id=_PEER, after_seq=0)
    assert event_fingerprint(staged[(_PEER, raw_parent.seq)]) == event_fingerprint(raw_parent)


def test_the_view_is_idempotent_and_passes_a_ledger_task_unchanged() -> None:
    """A viewed event comes back as the same object; a ledger task is never altered."""
    raw = StoredEvent(seq=1, ts=1.0, kind=EventKind.CLAIM, payload=_claim().as_persisted_dict())
    once = public_event_view(raw)
    assert public_event_view(once) is once
    ledger = StoredEvent(
        seq=2, ts=2.0, kind=EventKind.LEDGER_TASK, payload={"task_id": "t1", "via_peer": "x"}
    )
    assert public_event_view(ledger) is ledger
    chat = public_event_view(
        StoredEvent(seq=3, ts=3.0, kind=EventKind.CHAT, payload={"payload": "hello"})
    )
    assert public_event_view(chat) is chat


_CENSUS: dict[str, frozenset[str]] = {
    **dict.fromkeys(SNAPSHOT_KINDS, PUBLIC_CLAIM_FIELDS),
    EventKind.RELEASE: PUBLIC_CLAIM_FIELDS | {"via"},
    EventKind.LEDGER_PROGRESS: frozenset({"author", "kind", "posted_at", "task_id", "text"}),
    EventKind.ENTITLEMENT_ADVERT: frozenset(
        {"advert", "advertiser", "advertiser_key_id", "hub_id"}
    ),
    EventKind.MULTIHUB_PARTITION: frozenset(
        {"contesting_hubs", "local_hub_id", "namespace", "outcome", "owner_hub_id", "transition"}
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
    EventKind.DELIVERY_RECEIPT_IMMEDIATE: frozenset({"delivered", "message_seq"}),
    EventKind.DELIVERY_RECEIPT_DEFERRED: frozenset({"delivered", "message_seq"}),
    EventKind.DELIVERY_RECEIPT_EXPIRED: frozenset({"delivered", "message_seq"}),
    EventKind.DELIVERY_RECEIPT_REQUESTED: frozenset({"delivered", "message_seq"}),
}
"""The reviewed census: each served kind and the fields its follower consumers read."""

_OPERATION_RECORDS = (
    EventKind.OPERATION_DECLARED,
    EventKind.OPERATION_OUTCOME,
    EventKind.OPERATION_VERIFIED,
    EventKind.OPERATION_ATTEMPT_ANSWERED,
    EventKind.OPERATION_SEALED,
    EventKind.OPERATION_STOP_READBACK,
    EventKind.OPERATION_STOPPED,
    EventKind.OPERATION_RESUMED,
)


def test_the_served_kinds_are_exactly_the_reviewed_census() -> None:
    """Serving a new kind is a reviewed change to this census, never a default."""
    assert frozenset(_CENSUS) | set(_OPERATION_RECORDS) | {EventKind.LEDGER_TASK} == SERVED_KINDS


_PROBE_KEYS = frozenset().union(*_CENSUS.values()) | {
    "_notification",
    "approver_principal",
    "client_msg_id",
    "from",
    "message_id",
    "principal",
    "quota_principal",
    "recipients",
    "request_key",
    "sender",
    "session_id",
    "target",
    "to",
    "token_id",
    "via_peer",
}
"""Every listed field plus the private and neighbouring fields the key audit found."""


def test_every_listed_kind_keeps_exactly_its_listed_fields() -> None:
    """Each listed kind keeps exactly its consumers' fields, never one more or one fewer."""
    for kind, fields in _CENSUS.items():
        payload = dict.fromkeys(_PROBE_KEYS, "probe")
        view = public_event_view(StoredEvent(seq=7, ts=7.5, kind=kind, payload=payload))
        assert set(view.payload) == fields, kind
        assert (view.seq, view.ts, view.kind) == (7, 7.5, kind)


def test_an_operation_record_loses_only_its_request_key() -> None:
    """Fleet reads the operation records whole except the hub's own request key."""
    for kind in _OPERATION_RECORDS:
        payload = {"operation_id": "op-1", "sender": "a", "scope": "s", "request_key": "k-1"}
        view = public_event_view(StoredEvent(seq=3, ts=3.0, kind=kind, payload=payload))
        assert view.payload == {"operation_id": "op-1", "sender": "a", "scope": "s"}, kind


def test_a_kind_no_follower_reads_is_withheld_as_the_stub() -> None:
    """A withheld event keeps its sequence, time and kind; its payload is the constant stub."""
    relay = StoredEvent(
        seq=9,
        ts=9.25,
        kind=EventKind.OPERATOR_RELAY,
        payload={"approver_principal": "op:alice", "requester_principal": "op:bob"},
    )
    view = public_event_view(relay)
    assert (view.seq, view.ts, view.kind) == (9, 9.25, EventKind.OPERATOR_RELAY)
    assert view.payload == {"_withheld": PUBLIC_VIEW_VERSION}
    assert PUBLIC_VIEW_VERSION == "public-v2"
    assert is_withheld(view)
    assert not is_withheld(relay)
    assert public_event_view(view) is view


def test_a_kind_nobody_has_listed_yet_is_withheld_by_default() -> None:
    """A new kind stays private until the census lists it with its consumer."""
    future = StoredEvent(seq=1, ts=1.0, kind="a_kind_added_tomorrow", payload={"principal": "p"})
    assert is_withheld(public_event_view(future))


async def test_raw_rows_then_their_stubs_are_one_contiguous_history_to_a_follower(
    tmp_path: Path,
) -> None:
    """An owner upgraded between two reads serves withheld kinds as stubs: one history, no gap."""
    store = _store(tmp_path / "peer.db")
    store.append(EventKind.OPERATOR_RELAY, {"approver_principal": "op:alice", "relay": "r"})
    store.append(EventKind.GUARD_DENIAL, {"credential_principal_sha256": "ab" * 32})
    raw = tuple(store.read_all())
    store.close()
    served: list[Sequence[StoredEvent]] = [raw, tuple(public_event_view(e) for e in raw)]

    async def fetch(_cursor: int) -> Sequence[StoredEvent]:
        """Serve the whole log each time: raw first, then as an upgraded owner serves it."""
        return served.pop(0)

    follower = MultiHubFollower()
    first = await follower.poll(_PEER, fetch)
    second = await follower.poll(_PEER, fetch)
    assert first == second
    assert follower.cursor(_PEER) == raw[-1].seq


async def test_a_follower_keeps_and_shows_only_the_public_view_of_a_raw_peer(
    tmp_path: Path,
) -> None:
    """An older owner serves raw rows; the follower's observed claim still has no private field."""
    store = _store(tmp_path / "peer.db")
    follower = MultiHubFollower()
    observed = await follower.poll(_PEER, store_fetcher(store))
    store.close()
    claim = observed.observed_claims["t1"].claim
    assert dict(claim) == _claim().as_dict()
    assert not _PRIVATE & set(claim)
    assert not _PRIVATE & set(observed.to_dict()["observed_claims"]["t1"]["claim"])


async def test_raw_rows_then_their_public_view_are_one_history_to_a_follower(
    tmp_path: Path,
) -> None:
    """An owner upgraded between two reads re-serves the same rows projected: no equivocation."""
    store = _store(tmp_path / "peer.db")
    raw = tuple(store.read_all())
    store.close()
    served: list[Sequence[StoredEvent]] = [raw, tuple(public_event_view(e) for e in raw)]

    async def fetch(_cursor: int) -> Sequence[StoredEvent]:
        """Serve the whole log each time: raw first, then as an upgraded owner serves it."""
        return served.pop(0)

    follower = MultiHubFollower()
    first = await follower.poll(_PEER, fetch)
    second = await follower.poll(_PEER, fetch)
    assert first == second
    assert dict(second.observed_claims["t1"].claim) == _claim().as_dict()
