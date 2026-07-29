# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — task causal-parent value and payload regressions

from __future__ import annotations

import pytest

from synapse_channel.core.task_causality import (
    TASK_CAUSAL_PARENT_FIELD,
    TaskCausalParent,
    parse_task_causal_parent,
    parse_task_causal_parent_ref,
    task_event_payload,
    task_record_payload,
)


def _parent() -> TaskCausalParent:
    return TaskCausalParent("hub:west", 7, "a" * 64)


def test_parent_round_trips_mapping_and_colon_reference() -> None:
    parent = _parent()

    assert TaskCausalParent.from_value(parent) is parent
    assert TaskCausalParent.from_value(parent.to_dict()) == parent
    assert parse_task_causal_parent_ref(f"hub:west:7:{'a' * 64}") == parent
    assert parent.identity == ("hub:west", 7)
    assert parse_task_causal_parent(None) is None
    assert parse_task_causal_parent("") is None


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"hub_id": "hub", "seq": 1, "event_fingerprint": "a" * 64, "extra": True},
        {"hub_id": "", "seq": 1, "event_fingerprint": "a" * 64},
        {"hub_id": "hub", "seq": True, "event_fingerprint": "a" * 64},
        {"hub_id": "hub", "seq": 0, "event_fingerprint": "a" * 64},
        {"hub_id": "hub", "seq": 1 << 63, "event_fingerprint": "a" * 64},
        {"hub_id": 4, "seq": 1, "event_fingerprint": "a" * 64},
        {"hub_id": "hub", "seq": 1, "event_fingerprint": 4},
        {"hub_id": "\ud800", "seq": 1, "event_fingerprint": "a" * 64},
        {"hub_id": "hub", "seq": 1, "event_fingerprint": "A" * 64},
        {"hub_id": "hub", "seq": 1, "event_fingerprint": "a" * 63},
    ],
)
def test_parent_rejects_malformed_mapping(value: object) -> None:
    with pytest.raises(ValueError):
        parse_task_causal_parent(value)


@pytest.mark.parametrize(
    "value",
    ["hub:bad-seq:" + "a" * 64, "hub:1", "hub:0:" + "a" * 64],
)
def test_parent_rejects_malformed_cli_reference(value: str) -> None:
    with pytest.raises(ValueError):
        parse_task_causal_parent_ref(value)


def test_event_metadata_is_additive_and_stripped_from_task_projection() -> None:
    task = {"task_id": "T", "title": "secret", "version": 2}
    event = task_event_payload(task, _parent())

    assert event[TASK_CAUSAL_PARENT_FIELD] == _parent().to_dict()
    assert task_record_payload(event) == task
    assert TASK_CAUSAL_PARENT_FIELD not in task
    assert task_event_payload(task, None) == task


def test_direct_constructor_rejects_non_string_fields() -> None:
    with pytest.raises(ValueError, match="causal_parent must be an object"):
        TaskCausalParent.from_value(None)
    with pytest.raises(ValueError, match="hub_id must be a string"):
        TaskCausalParent(4, 1, "a" * 64)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event_fingerprint must be a string"):
        TaskCausalParent("hub", 1, 4)  # type: ignore[arg-type]
