# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the description of one vendor-client operation
"""Descriptions of operations: vocabularies, typed arguments, plain-data form."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.operations.spec import (
    QUALIFICATIONS,
    RUNNABLE,
    ArgumentSpec,
    OperationSpec,
    OperationSpecError,
)

SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"


def queue_message(**changes: Any) -> OperationSpec:
    """Return the description of queueing a message into a session."""
    fields: dict[str, Any] = {
        "vendor": "codex",
        "client_version": "0.159.1",
        "os": "linux",
        "host": "workstation",
        "operation": "session.queue_message",
        "transport": "cli",
        "scope": "session",
        "required_authority": "ops.session.write",
        "arguments": (
            ArgumentSpec("thread", "uuid"),
            ArgumentSpec("priority", "enum", required=False, choices=("normal", "high")),
            ArgumentSpec("attempts", "integer", required=False),
            ArgumentSpec("dry_run", "boolean", required=False),
            ArgumentSpec("note", "string", required=False),
            ArgumentSpec("api_key", "secret_ref", required=False),
        ),
        "side_effects": ("a message is queued into the session",),
        "idempotent": "no",
        "qualification": "run_once",
        "evidence": "planning/CODEX_NATIVE_QUEUE_QUALIFICATION_2026-10-04",
    }
    fields.update(changes)
    return OperationSpec(**fields)


def test_a_description_has_a_key_and_says_whether_it_may_run() -> None:
    spec = queue_message()

    assert spec.key == ("workstation", "codex", "0.159.1", "linux", "session.queue_message")
    assert spec.runnable and spec.interactive == "no" and not spec.needs_restart
    assert QUALIFICATIONS == ("unverified", "help_text", "run_once", "accepted")
    assert RUNNABLE == {"run_once", "accepted"}
    for level in ("unverified", "help_text"):
        assert not queue_message(qualification=level, evidence="").runnable
    assert queue_message(qualification="accepted").runnable


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"vendor": "co dex"}, "operation vendor is a short token"),
        ({"client_version": ""}, "operation client_version is a short token"),
        ({"os": "linux\n"}, "operation os is a short token"),
        ({"host": "a/b"}, "operation host is a short token"),
        ({"required_authority": ""}, "operation required_authority is a short token"),
        ({"operation": "queue"}, "dotted lower-case name"),
        ({"operation": "Session.Queue"}, "dotted lower-case name"),
        ({"operation": "session..queue"}, "dotted lower-case name"),
        ({"transport": "shell"}, "operation transport is not a known value"),
        ({"scope": "world"}, "operation scope is not a known value"),
        ({"interactive": "maybe"}, "operation interactive is not a known value"),
        ({"idempotent": "sometimes"}, "operation idempotent is not a known value"),
        ({"qualification": "trusted"}, "operation qualification is not a known value"),
        ({"needs_restart": "no"}, "needs_restart is true or false"),
        ({"qualification": "run_once", "evidence": "  "}, "names its evidence"),
        ({"qualification": "accepted", "evidence": ""}, "names its evidence"),
        (
            {"arguments": (ArgumentSpec("thread", "uuid"), ArgumentSpec("thread", "string"))},
            "names each argument once",
        ),
    ],
)
def test_a_description_outside_the_vocabularies_is_refused(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(OperationSpecError, match=message):
        queue_message(**changes)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"name": "Thread", "type": "uuid"}, "argument name is lower-case"),
        ({"name": "thread", "type": "path"}, "unknown type"),
        ({"name": "mode", "type": "enum"}, "choices belong to an enum"),
        ({"name": "mode", "type": "string", "choices": ("a",)}, "choices belong to an enum"),
        ({"name": "mode", "type": "string", "required": "yes"}, "required is true or false"),
    ],
)
def test_an_argument_description_that_could_not_validate_is_refused(
    fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(OperationSpecError, match=message):
        ArgumentSpec(**fields)


def test_arguments_become_typed_values() -> None:
    spec = queue_message()

    values = spec.arguments_from(
        {
            "thread": SESSION,
            "priority": "high",
            "attempts": "-3",
            "dry_run": "false",
            "note": "",
            "api_key": "ref:vault/codex-key",
        }
    )

    assert values == {
        "thread": SESSION,
        "priority": "high",
        "attempts": -3,
        "dry_run": False,
        "note": "",
        "api_key": "ref:vault/codex-key",
    }
    assert spec.arguments_from({"thread": SESSION, "dry_run": "true"}) == {
        "thread": SESSION,
        "dry_run": True,
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "needs the argument thread"),
        ({"thread": SESSION, "extra": "1"}, "not an argument of session.queue_message: extra"),
        ({"thread": SESSION.upper()}, "argument thread: not a valid uuid"),
        ({"thread": "night-shift"}, "argument thread: not a valid uuid"),
        ({"thread": SESSION, "priority": "urgent"}, "argument priority: not a valid enum"),
        ({"thread": SESSION, "attempts": "1.5"}, "argument attempts: not a valid integer"),
        ({"thread": SESSION, "attempts": "9" * 19}, "argument attempts: not a valid integer"),
        ({"thread": SESSION, "dry_run": "yes"}, "argument dry_run: not a valid boolean"),
        ({"thread": SESSION, "note": "x" * 4097}, "argument note: a value is text of bounded"),
        ({"thread": SESSION, "note": 5}, "argument note: a value is text of bounded"),
        ({"thread": SESSION, "api_key": "sk-live-not-a-reference"}, "not a valid secret_ref"),
        ({"thread": SESSION, "api_key": "ref:"}, "not a valid secret_ref"),
    ],
)
def test_arguments_that_do_not_fit_are_refused(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(OperationSpecError, match=message):
        queue_message().arguments_from(raw)


def test_a_description_survives_the_trip_through_plain_data() -> None:
    spec = queue_message(needs_restart=True, interactive="operator_step", idempotent="reconcilable")

    data = json.loads(json.dumps(spec.to_json()))

    assert OperationSpec.from_json(data) == spec
    assert data["arguments"][1] == {
        "name": "priority",
        "type": "enum",
        "required": False,
        "choices": ["normal", "high"],
        "description": "",
    }
    bare = replace(spec, arguments=(), side_effects=())
    minimal = {k: v for k, v in bare.to_json().items() if k not in {"arguments", "side_effects"}}
    assert OperationSpec.from_json(minimal) == bare


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: [data],
        lambda data: {**data, "unknown_field": 1},
        lambda data: {k: v for k, v in data.items() if k != "vendor"},
        lambda data: {**data, "arguments": [{"type": "uuid"}]},
        lambda data: {**data, "arguments": ["thread"]},
        lambda data: {**data, "arguments": 3},
        lambda data: {**data, "side_effects": 3},
        lambda data: {**data, "scope": "world"},
    ],
)
def test_plain_data_that_is_not_a_description_is_refused(damage: Any) -> None:
    with pytest.raises(OperationSpecError):
        OperationSpec.from_json(damage(queue_message().to_json()))
