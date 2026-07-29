# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-bound multi-hub event identity regressions

from __future__ import annotations

from collections.abc import Mapping

import pytest

from synapse_channel.core.multihub_equivocation import (
    FederationQuarantine,
    MultiHubEquivocationError,
    MultiHubPeerQuarantinedError,
    MultiHubSequenceError,
    event_fingerprint,
    remember_content_bound_event,
    valid_fingerprint,
    validate_content_bound_batch,
)
from synapse_channel.core.multihub_merge import HubEvent


def _event(**changes: object) -> HubEvent:
    values: dict[str, object] = {
        "hub_id": "peer-a",
        "seq": 7,
        "ts": 12.5,
        "kind": "ledger_task",
        "payload": {"nested": {"ok": True}, "items": [None, 2, 3.5, "x"]},
    }
    values.update(changes)
    return HubEvent(**values)  # type: ignore[arg-type]


def test_fingerprint_is_stable_across_mapping_insertion_order() -> None:
    first = _event(payload={"b": 2, "a": {"z": False, "y": [1, 2]}})
    second = _event(payload={"a": {"y": [1, 2], "z": False}, "b": 2})

    assert event_fingerprint(first) == event_fingerprint(second)
    assert valid_fingerprint(event_fingerprint(first))


@pytest.mark.parametrize(
    "changed",
    [
        {"hub_id": "peer-b"},
        {"seq": 8},
        {"ts": 12.75},
        {"kind": "claim"},
        {"payload": {"nested": {"ok": False}}},
    ],
)
def test_fingerprint_binds_every_security_relevant_field(changed: dict[str, object]) -> None:
    assert event_fingerprint(_event()) != event_fingerprint(_event(**changed))


def test_exact_duplicate_is_idempotent_but_conflicting_content_raises() -> None:
    accepted = _event()
    events = {accepted.identity: accepted}

    remember_content_bound_event(events, _event())
    assert events == {accepted.identity: accepted}

    with pytest.raises(MultiHubEquivocationError) as raised:
        remember_content_bound_event(events, _event(payload={"secret": "not persisted"}))
    error = raised.value
    assert error.peer_id == "peer-a"
    assert error.seq == 7
    assert valid_fingerprint(error.accepted_fingerprint)
    assert valid_fingerprint(error.conflicting_fingerprint)
    assert "secret" not in str(error)


def test_first_event_is_validated_before_it_enters_the_union() -> None:
    events: dict[tuple[str, int], HubEvent] = {}
    malformed = _event(payload={"unsupported": object()})

    with pytest.raises(ValueError, match="unsupported multi-hub event payload type"):
        remember_content_bound_event(events, malformed)
    assert events == {}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"hub_id": ""}, "hub_id"),
        ({"seq": True}, "sequence"),
        ({"seq": 0}, "sequence"),
        ({"ts": "now"}, "timestamp"),
        ({"ts": float("nan")}, "finite"),
        ({"kind": ""}, "kind"),
        ({"payload": []}, "payload must be a mapping"),
        ({"payload": {1: "bad"}}, "keys must be strings"),
        ({"payload": {"bad": float("inf")}}, "floats must be finite"),
        ({"payload": {"bad": (1, 2)}}, "unsupported"),
    ],
)
def test_fingerprint_rejects_noncanonical_event_shapes(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        event_fingerprint(_event(**changes))


def test_quarantine_shape_and_error_never_carry_event_payloads() -> None:
    quarantine = FederationQuarantine(
        peer_id="peer-a",
        seq=7,
        accepted_fingerprint="a" * 64,
        conflicting_fingerprint="b" * 64,
        detected_at=123.0,
        observer_id="hub-local",
    )

    assert quarantine.to_dict() == {
        "peer_id": "peer-a",
        "seq": 7,
        "accepted_fingerprint": "a" * 64,
        "conflicting_fingerprint": "b" * 64,
        "detected_at": 123.0,
        "observer_id": "hub-local",
        "status": "quarantined",
    }
    error = MultiHubPeerQuarantinedError(quarantine)
    assert error.quarantine is quarantine
    assert error.code == "multihub_peer_quarantined"


def test_digest_validator_rejects_wrong_type_case_length_and_alphabet() -> None:
    assert not valid_fingerprint(None)
    assert not valid_fingerprint("A" * 64)
    assert not valid_fingerprint("a" * 63)
    assert not valid_fingerprint("z" * 64)


def test_recursive_and_invalid_unicode_payloads_fail_closed() -> None:
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError, match="cannot be fingerprinted"):
        event_fingerprint(_event(payload=recursive))
    with pytest.raises(ValueError, match="cannot be fingerprinted"):
        event_fingerprint(_event(payload={"bad": "\ud800"}))


@pytest.mark.parametrize("changes", [{"hub_id": "\ud800"}, {"kind": "\ud800"}])
def test_invalid_unicode_identity_fields_fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="identity must be valid UTF-8"):
        event_fingerprint(_event(**changes))


def test_structural_protocol_accepts_an_immutable_mapping_payload() -> None:
    class _Event:
        hub_id = "peer"
        seq = 1
        ts = 1.0
        kind = "claim"
        payload: Mapping[str, object] = {"ok": True}

    assert valid_fingerprint(event_fingerprint(_Event()))


def test_batch_requires_contiguous_new_sequences() -> None:
    events: dict[tuple[str, int], HubEvent] = {}
    batch = (_event(seq=1), _event(seq=3))

    with pytest.raises(MultiHubSequenceError) as raised:
        validate_content_bound_batch(events, batch, peer_id="peer-a", after_seq=0)

    assert raised.value.expected_seq == 2
    assert raised.value.observed_seq == 3
    assert events == {}


def test_batch_rejects_an_unseen_sequence_at_or_below_cursor() -> None:
    events = {("peer-a", 2): _event(seq=2)}

    with pytest.raises(MultiHubSequenceError, match="expected sequence 3"):
        validate_content_bound_batch(
            events,
            (_event(seq=1),),
            peer_id="peer-a",
            after_seq=2,
        )


def test_batch_allows_exact_duplicates_in_any_position() -> None:
    first = _event(seq=1)
    second = _event(seq=2)
    events: dict[tuple[str, int], HubEvent] = {}

    validate_content_bound_batch(
        events,
        (first, first, second, first),
        peer_id="peer-a",
        after_seq=0,
    )

    assert events == {("peer-a", 1): first, ("peer-a", 2): second}


def test_batch_rejects_a_wrong_peer_tag() -> None:
    with pytest.raises(ValueError, match="does not match"):
        validate_content_bound_batch(
            {},
            (_event(hub_id="west"),),
            peer_id="east",
            after_seq=0,
        )
