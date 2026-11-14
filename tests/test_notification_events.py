# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — trusted assignment routing metadata tests
"""Qualify public native notification selection and its self-noise refusals."""

from __future__ import annotations

import pytest

from synapse_channel.core.notification_events import (
    NOTIFICATION_FIELD,
    event_activity,
    notification_metadata,
    task_notification,
)


def test_assignment_metadata_is_content_free_and_exact() -> None:
    task: dict[str, object] = {
        "task_id": "ALPHA/work",
        "project": "ALPHA",
        "title": "private task text",
        "suggested_owner": "ALPHA/bot",
        "status": "open",
    }
    pointer = task_notification(task, "ALPHA/operator", "")
    assert pointer == {
        "version": 1,
        "kind": "tasks",
        "actor": "ALPHA/operator",
        "recipient": "ALPHA/bot",
    }
    event = {**task, NOTIFICATION_FIELD: pointer}
    assert event_activity("ledger_task", event, "ALPHA/bot") == "tasks"
    assert event_activity("ledger_task", event, "ALPHA/other") is None
    assert task_notification(task, "ALPHA/operator", "ALPHA/bot") is None
    returned = task_notification(task, "ALPHA/operator", "ALPHA/bot", previous_status="done")
    assert returned == pointer
    assert (
        task_notification(task, "ALPHA/operator", "ALPHA/bot", previous_status="cancelled")
        == returned
    )
    assert task_notification(task, "ALPHA/bot", "ALPHA/bot", previous_status="done") is None
    assert task_notification(task, "ALPHA/operator", "ALPHA/bot", previous_status="open") is None
    assert (
        task_notification(
            {**task, "status": "cancelled"},
            "ALPHA/operator",
            "ALPHA/bot",
            previous_status="done",
        )
        is None
    )
    assert task_notification({**task, "status": "done"}, "ALPHA/operator", "") is None
    assert task_notification({**task, "status": "cancelled"}, "ALPHA/operator", "") is None
    assert task_notification({**task, "suggested_owner": None}, "ALPHA/operator", "") is None


@pytest.mark.parametrize(
    ("actor", "recipient", "payload"),
    [
        ("", "ALPHA/bot", {"task_id": "ALPHA/work"}),
        ("x" * 257, "ALPHA/bot", {"task_id": "ALPHA/work"}),
        ("ALPHA/bot", "ALPHA/bot", {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", "ALPHA/" + "x" * 251, {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", " ALPHA/bot", {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", "bot", {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", "/bot", {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", "ALPHA/", {"task_id": "ALPHA/work"}),
        ("ALPHA/operator", "ALPHA/bot", {"task_id": None}),
        ("ALPHA/operator", "ALPHA/bot", {"task_id": "BETA/work"}),
        ("ALPHA/operator", "ALPHA/bot", {"task_id": "ALPHA/work", "project": "BETA"}),
    ],
)
def test_ineligible_assignment_never_gets_routing_metadata(
    actor: str, recipient: str, payload: dict[str, object]
) -> None:
    assert notification_metadata("tasks", actor, recipient, payload) is None


@pytest.mark.parametrize(
    "pointer",
    [
        None,
        {},
        {"version": True},
        {"version": 1, "actor": [], "recipient": "ALPHA/bot"},
        {"version": 1, "actor": "ALPHA/operator", "recipient": "ALPHA/foreign"},
        {"version": 2, "kind": "handoffs", "actor": "ALPHA/operator", "recipient": "ALPHA/bot"},
        {"version": 1, "kind": "tasks", "actor": "ALPHA/operator", "recipient": "ALPHA/bot"},
        {"version": 1, "kind": "handoffs", "actor": "ALPHA/bot", "recipient": "ALPHA/bot"},
        {
            "version": 1,
            "kind": "handoffs",
            "actor": "ALPHA/operator",
            "recipient": "ALPHA/bot",
            "injected": True,
        },
    ],
)
def test_untrusted_or_legacy_handoff_pointer_is_not_inferred(pointer: object) -> None:
    payload = {"task_id": "ALPHA/work", NOTIFICATION_FIELD: pointer}
    assert event_activity("handoff", payload, "ALPHA/bot") is None


def test_handoff_and_direct_messages_select_only_exact_identity() -> None:
    payload: dict[str, object] = {"task_id": "ALPHA/work"}
    payload[NOTIFICATION_FIELD] = notification_metadata(
        "handoffs", "ALPHA/operator", "ALPHA/bot", payload
    )
    assert event_activity("handoff", payload, "ALPHA/bot") == "handoffs"
    assert event_activity("checkpoint", payload, "ALPHA/bot") is None
    assert (
        event_activity("chat", {"sender": "ALPHA/operator", "target": "ALPHA/bot"}, "ALPHA/bot")
        == "messages"
    )
    refused: list[dict[str, object]] = [
        {"sender": "ALPHA/operator", "target": "all"},
        {"sender": "ALPHA/operator", "target": ["ALPHA/bot"]},
        {"sender": "ALPHA/bot", "target": "ALPHA/bot"},
        {"sender": "ALPHA/operator", "target": "ALPHA/bot", "channel": "private"},
    ]
    for fields in refused:
        assert event_activity("chat", fields, "ALPHA/bot") is None


@pytest.mark.parametrize("frame_type", ["open", "chunk", "done", "abort"])
def test_stream_activity_waits_for_terminal_frame(frame_type: str) -> None:
    payload = {
        "sender": "ALPHA/operator",
        "target": "ALPHA/bot",
        "kind": "stream",
        "frame_type": frame_type,
    }
    expected = None if frame_type in ("open", "chunk") else "messages"
    assert event_activity("chat", payload, "ALPHA/bot") == expected
