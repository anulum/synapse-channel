# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard operator write-path unit tests

"""Direct tests for the operator write-path in dashboard_operator_writes.

The HTTP route tests prove the wire behaviour; this surface proves the module
contract — media-type gating, body decoding, per-field validation reasons, and
the relay outcome-to-status map — without a socket. Relay execution runs
against a drop-in relay class (the suite's established stub-class pattern),
never a patched transport.
"""

from __future__ import annotations

import io
import json
from http import HTTPStatus
from typing import Any

import pytest

import synapse_channel.dashboard_operator_writes as operator_writes
from synapse_channel.dashboard_feed_serving import FeedResponse
from synapse_channel.dashboard_operator import (
    ACCEPTED,
    DELIVERED,
    DENIED,
    REJECTED,
    UNREACHABLE,
    RelayOutcome,
)
from synapse_channel.dashboard_operator_writes import (
    MAX_OPERATOR_BODY_BYTES,
    RelayPlan,
    execute_relay,
    is_json_media_type,
    plan_message,
    plan_task,
    plan_task_update,
    read_operator_body,
)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("application/json", True),
        ("application/json; charset=utf-8", True),
        ("APPLICATION/JSON", True),
        ("text/plain", False),
        ("", False),
        ("application/jsonp", False),
    ],
)
def test_is_json_media_type_ignores_parameters_and_case(header: str, expected: bool) -> None:
    assert is_json_media_type(header) is expected


def _body_bytes(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


def test_read_operator_body_returns_the_decoded_object() -> None:
    raw = _body_bytes({"to": "all", "text": "hi"})
    body = read_operator_body(str(len(raw)), io.BytesIO(raw))
    assert body == {"to": "all", "text": "hi"}


@pytest.mark.parametrize(
    ("length", "raw"),
    [
        (None, b""),  # missing Content-Length
        ("abc", b""),  # non-numeric length
        ("0", b""),  # empty body
        ("-5", b""),  # negative length
        (str(MAX_OPERATOR_BODY_BYTES + 1), b"x"),  # over the size bound
        ("9", b"not-json!"),  # undecodable payload
        ("6", _body_bytes([1, 2])),  # JSON, but not an object
        ("4", b"\xff\xfe\xfd\xfc"),  # invalid UTF-8
    ],
)
def test_read_operator_body_answers_none_for_every_unusable_body(
    length: str | None, raw: bytes
) -> None:
    assert read_operator_body(length, io.BytesIO(raw)) is None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "'to' must be a non-empty string"),
        ({"to": " ", "text": "hi"}, "'to' must be a non-empty string"),
        ({"to": "all"}, "'text' must be a non-empty string"),
        ({"to": "all", "text": 7}, "'text' must be a non-empty string"),
    ],
)
def test_plan_message_names_the_offending_field(body: dict[str, Any], reason: str) -> None:
    assert plan_message(body) == reason


def test_plan_message_strips_the_target_but_not_the_text() -> None:
    plan = plan_message({"to": "  ops ", "text": "keep  spacing "})
    assert isinstance(plan, RelayPlan)
    assert plan.action == "message"
    assert plan.extra == {"to": "ops"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "'id' must be a non-empty string"),
        ({"id": "T"}, "'title' must be a non-empty string"),
        ({"id": "T", "title": "t", "depends_on": "X"}, "'depends_on' must be a list of strings"),
        ({"id": "T", "title": "t", "depends_on": [1]}, "'depends_on' must be a list of strings"),
    ],
)
def test_plan_task_names_the_offending_field(body: dict[str, Any], reason: str) -> None:
    assert plan_task(body) == reason


def test_plan_task_normalises_id_and_dependencies() -> None:
    plan = plan_task({"id": " T1 ", "title": "wire", "depends_on": [" A ", "", "B"]})
    assert isinstance(plan, RelayPlan)
    assert plan.action == "task"
    assert plan.extra == {"id": "T1"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "'id' must be a non-empty string"),
        ({"id": "T", "status": " "}, "'status' must be a non-empty string when present"),
        ({"id": "T", "note": 5}, "'note' must be a non-empty string when present"),
        ({"id": "T"}, "a task update needs at least one of 'status' or 'note'"),
    ],
)
def test_plan_task_update_names_the_offending_field(body: dict[str, Any], reason: str) -> None:
    assert plan_task_update(body) == reason


def test_plan_task_update_accepts_status_or_note_alone() -> None:
    with_status = plan_task_update({"id": "T", "status": "done"})
    with_note = plan_task_update({"id": "T", "note": "progress"})
    assert isinstance(with_status, RelayPlan)
    assert isinstance(with_note, RelayPlan)
    assert with_status.action == "task_update"
    assert with_status.extra == {"id": "T"}


class _StubRelay:
    """Drop-in OperatorRelay yielding a fixed outcome without a hub."""

    outcome: RelayOutcome

    def __init__(self, **_kwargs: object) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def relay_message(self, to: str, text: str) -> RelayOutcome:
        self.calls.append(("message", (to, text)))
        return self.outcome


def _stubbed_execute(monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome) -> FeedResponse:
    stub = type("_Outcome", (_StubRelay,), {"outcome": outcome})
    monkeypatch.setattr(operator_writes, "OperatorRelay", stub)
    plan = plan_message({"to": "all", "text": "hi"})
    assert isinstance(plan, RelayPlan)
    return execute_relay(
        plan,
        uri="ws://127.0.0.1:1",
        operator_name="op",
        token=None,
        ready_timeout=0.1,
        response_timeout=0.1,
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RelayOutcome(DELIVERED, "delivered"), HTTPStatus.OK),
        (RelayOutcome(ACCEPTED, "accepted"), HTTPStatus.OK),
        (RelayOutcome(DENIED, "denied"), HTTPStatus.FORBIDDEN),
        (RelayOutcome(REJECTED, "rejected"), HTTPStatus.CONFLICT),
        (RelayOutcome(UNREACHABLE, "unreachable"), HTTPStatus.SERVICE_UNAVAILABLE),
    ],
)
def test_execute_relay_maps_each_outcome_to_its_status(
    monkeypatch: pytest.MonkeyPatch, outcome: RelayOutcome, expected: HTTPStatus
) -> None:
    response = _stubbed_execute(monkeypatch, outcome)
    assert response.status == expected
    document = json.loads(response.body)
    assert document["action"] == "message"
    assert document["to"] == "all"
    assert document["status"] == outcome.status


def test_execute_relay_maps_a_transport_exception_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RaisingRelay(_StubRelay):
        async def relay_message(self, to: str, text: str) -> RelayOutcome:
            raise OSError("connection reset")

    monkeypatch.setattr(operator_writes, "OperatorRelay", _RaisingRelay)
    plan = plan_message({"to": "all", "text": "hi"})
    assert isinstance(plan, RelayPlan)
    response = execute_relay(
        plan,
        uri="ws://127.0.0.1:1",
        operator_name="op",
        token=None,
        ready_timeout=0.1,
        response_timeout=0.1,
    )
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert b"operator relay failed: connection reset" in response.body
