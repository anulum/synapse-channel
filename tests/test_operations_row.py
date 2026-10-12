# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for one catalogue row and its native binding
"""A catalogue row: identity, binding, arguments, effects, qualification, plain data."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.operations.row import (
    AUTHORITY_OF_EFFECT,
    EFFECTS,
    ArgumentBinding,
    CallerObligation,
    NativeBinding,
    OperationRow,
)
from synapse_channel.operations.spec import OperationSpecError

SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"
FINGERPRINT = "sha256:" + "ab" * 32


def list_sessions(**changes: Any) -> OperationRow:
    """Return a command-line row: list the sessions of one client, as JSON."""
    fields: dict[str, Any] = {
        "host": "workstation",
        "vendor": "kimi",
        "component": "cli",
        "component_version": "2.1.1",
        "os": "linux",
        "operation": "session.list",
        "transport": "cli",
        "scope": "user",
        "effects": ("read",),
        "binding": NativeBinding(
            "session list", executable="/opt/kimi/bin/kimi", fixed=("session", "list", "--json")
        ),
        "arguments": (
            ArgumentBinding("cwd", "string", "flag", "--cwd", required=False),
            ArgumentBinding("all", "boolean", "switch", "--all", required=False),
            ArgumentBinding(
                "limit", "integer", "flag", "--limit", required=False, minimum=1, maximum=500
            ),
        ),
        "reserved_natives": ("--config",),
        "output_mode": "json",
        "output_schema": "kimi.session-list.v1",
        "idempotent": "yes",
        "version_printed": "2.1.1",
    }
    fields.update(changes)
    return OperationRow(**fields)


def read_session(**changes: Any) -> OperationRow:
    """Return a request row: read one session from a client's running service."""
    fields: dict[str, Any] = {
        "host": "workstation",
        "vendor": "codex",
        "component": "app-server",
        "component_version": "0.159.1",
        "os": "linux",
        "operation": "session.read",
        "transport": "rpc",
        "scope": "session",
        "effects": ("read",),
        "binding": NativeBinding("thread/read", method="thread/read"),
        "arguments": (
            ArgumentBinding("session", "uuid", "parameter", "threadId"),
            ArgumentBinding("include_turns", "boolean", "parameter", "includeTurns", False),
            ArgumentBinding("sources", "string", "parameter", "sourceKinds", False, max_items=3),
        ),
        "output_mode": "json",
        "disclosures": ("session_content",),
        "idempotent": "yes",
    }
    fields.update(changes)
    return OperationRow(**fields)


def qualified(row: OperationRow, level: str = "run_once") -> OperationRow:
    """Return ``row`` as qualified by a run of exactly its present binding."""
    bound = replace(row, fingerprint=FINGERPRINT)
    return replace(
        bound,
        qualification=level,
        evidence="evidence/run-1",
        qualified_binding=bound.binding_digest,
    )


def test_a_row_is_identified_by_component_transport_and_variant() -> None:
    row = list_sessions()

    assert row.key == (
        "workstation",
        "kimi",
        "cli",
        "2.1.1",
        "linux",
        "session.list",
        "cli",
        "default",
    )
    assert list_sessions(variant="archived").key != row.key
    assert read_session().key[2:4] == ("app-server", "0.159.1")
    assert list_sessions(operation="plugin.marketplace.list").operation.count(".") == 2


def test_every_effect_needs_its_own_permission_and_two_have_none() -> None:
    assert EFFECTS == (
        "read",
        "change",
        "execute",
        "extend",
        "approve",
        "host",
        "destroy",
        "access",
    )
    assert set(EFFECTS) - set(AUTHORITY_OF_EFFECT) == {"host", "access"}
    assert all(name == f"operation-{effect}" for effect, name in AUTHORITY_OF_EFFECT.items())

    compound = list_sessions(effects=("read", "change", "destroy"))
    assert compound.required_authorities == (
        "operation-read",
        "operation-change",
        "operation-destroy",
    )
    # The class shown is one of the effects and decides nothing.
    assert compound.display_class == "destroy" and list_sessions().display_class == "read"
    assert list_sessions(effects=("read", "host")).required_authorities == ("operation-read",)
    assert list_sessions(effects=("access",)).required_authorities == ()
    assert not compound.needs_human_principal
    assert list_sessions(effects=("read", "extend")).needs_human_principal
    assert list_sessions(effects=("approve",)).needs_human_principal


def test_only_a_run_of_exactly_this_binding_makes_a_row_runnable() -> None:
    plain = list_sessions()
    ready = qualified(plain)

    assert not plain.runnable and not plain.stale
    assert not list_sessions(qualification="help_text").runnable
    assert ready.runnable and not ready.stale
    assert qualified(plain, "accepted").runnable
    # Anything the qualification vouches for changes the digest: the row is stale.
    bound: tuple[dict[str, Any], ...] = (
        {"binding": replace(ready.binding, fixed=("session", "list"))},
        {"binding": replace(ready.binding, executable="/usr/bin/kimi")},
        {"arguments": ready.arguments[:2]},
        {"reserved_natives": ()},
        {"effects": ("read", "change")},
        {"fingerprint": "sha256:" + "cd" * 32},
        {"component_version": "2.1.2"},
        {"output_mode": "text", "output_schema": ""},
        {"scope": "host"},
    )
    for change in bound:
        changed = replace(ready, **change)
        assert changed.stale and not changed.runnable, change
    # What it does not vouch for leaves the row runnable.
    unbound: tuple[dict[str, Any], ...] = (
        {"evidence": "evidence/run-2"},
        {"side_effects": ("none",)},
        {"version_printed": "kimi 2.1.1"},
        {"qualification": "accepted"},
    )
    for change in unbound:
        assert replace(ready, **change).runnable, change


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"qualification": "run_once", "evidence": "e", "fingerprint": FINGERPRINT}, "names its"),
        ({"qualification": "run_once", "evidence": " ", "qualified_binding": FINGERPRINT}, "names"),
        ({"qualification": "accepted", "evidence": "e", "qualified_binding": FINGERPRINT}, "names"),
    ],
)
def test_a_qualified_row_names_evidence_fingerprint_and_binding(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(OperationSpecError, match=message):
        list_sessions(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"host": "a/b"}, "row host is not in the form"),
        ({"vendor": ""}, "row vendor is not in the form"),
        ({"component": "app server"}, "row component is not in the form"),
        ({"component_version": 7}, "row component_version is text"),
        ({"os": "linux\n"}, "row os is not in the form"),
        ({"variant": ""}, "row variant is not in the form"),
        ({"version_printed": "v" * 501}, "row version_printed is text of at most 500"),
        ({"evidence": 3}, "row evidence is text"),
        ({"operation": "session"}, "two or three parts"),
        ({"operation": "a.b.c.d"}, "two or three parts"),
        ({"operation": "Session.List"}, "two or three parts"),
        ({"operation": ["session.list"]}, "two or three parts"),
        ({"transport": "shell"}, "row transport is not a known value"),
        ({"scope": "world"}, "row scope is not a known value"),
        ({"interactive": "maybe"}, "row interactive is not a known value"),
        ({"idempotent": "sometimes"}, "row idempotent is not a known value"),
        ({"qualification": "trusted"}, "row qualification is not a known value"),
        ({"output_mode": "html"}, "row output_mode is not a known value"),
        ({"needs_restart": "no"}, "needs_restart is true or false"),
        ({"side_effects": ["x"]}, "row side_effects is a list"),
        ({"side_effects": (3,)}, "each of row side_effects is text"),
        ({"reserved_natives": ("a b",)}, "each of row reserved_natives is not in the form"),
        ({"output_schema": "a b"}, "row output_schema is not in the form"),
        ({"output_projection": "a/b"}, "row output_projection is not in the form"),
        ({"fingerprint": "sha256:xyz"}, "row fingerprint is not in the form"),
        ({"qualified_binding": "md5:1"}, "row qualified_binding is not in the form"),
        ({"effects": ()}, "at least one effect"),
        ({"effects": ("change", "read")}, "row effects lists known values once each"),
        ({"effects": ("read", "read")}, "row effects lists known values once each"),
        ({"effects": ("observe",)}, "row effects lists known values once each"),
        ({"effects": ["read"]}, "row effects is a list"),
        ({"disclosures": ("session_content", "credential_bearing")}, "row disclosures lists"),
        ({"disclosures": ("everything",)}, "row disclosures lists known values"),
        ({"output_mode": "text"}, "an output schema belongs to json output"),
        ({"binding": {"native_name": "x"}}, "row binding is a native binding"),
        ({"arguments": [ArgumentBinding("a", "string", "flag", "--a")]}, "row arguments is a list"),
        ({"arguments": ("cwd",)}, "each of row arguments is a description"),
        ({"obligations": ("ask",)}, "each of row obligations is a description"),
        (
            {"arguments": (ArgumentBinding("a", "string", "flag", "--a"),) * 65},
            "row arguments is a list of at most 64",
        ),
    ],
)
def test_a_row_outside_the_vocabularies_is_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(OperationSpecError, match=message):
        list_sessions(**changes)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: list_sessions(binding=NativeBinding("x", method="x")), "names an executable"),
        (lambda: list_sessions(binding=NativeBinding("x", "kimi", method="x")), "a method, alone"),
        (lambda: read_session(binding=NativeBinding("x", executable="codex")), "names a method"),
        (lambda: read_session(binding=NativeBinding("x")), "names a method"),
        (
            lambda: read_session(binding=NativeBinding("x", fixed=("a",), method="x")),
            "fixed words belong to a command-line row",
        ),
        (
            lambda: read_session(transport="pane", binding=NativeBinding("/x", method="/x")),
            "a pane row takes no arguments",
        ),
        (
            lambda: list_sessions(obligations=(CallerObligation("item/approval", "refuse"),)),
            "caller obligations belong to a request transport",
        ),
        (
            lambda: read_session(obligations=(CallerObligation("ask", "refuse"),) * 2),
            "names each obligation once",
        ),
        (
            lambda: read_session(obligations=(CallerObligation("ask", "answer_with_consent"),)),
            "answering with consent needs the effect approve",
        ),
    ],
)
def test_a_binding_that_does_not_fit_its_transport_is_refused(build: Any, message: str) -> None:
    with pytest.raises(OperationSpecError, match=message):
        build()


def test_a_request_row_states_how_each_request_of_the_component_ends() -> None:
    obligations = (
        CallerObligation("item/commandExecution/requestApproval", "refuse"),
        CallerObligation("item/tool/requestUserInput", "requires_interaction"),
        CallerObligation("item/tool/call", "error"),
        CallerObligation("item/permissions/requestApproval", "answer_with_consent"),
    )

    row = read_session(effects=("read", "execute", "approve"), obligations=obligations)
    pane = read_session(
        transport="pane", arguments=(), binding=NativeBinding("/status", method="/status")
    )

    assert [item.disposition for item in row.obligations] == [
        "refuse",
        "requires_interaction",
        "error",
        "answer_with_consent",
    ]
    assert pane.binding.method == "/status" and pane.arguments == ()
    for fields, message in (
        ({"request": "a b", "disposition": "refuse"}, "an obligation's request is not in the form"),
        ({"request": "ask", "disposition": "approve"}, "obligation ask: unknown disposition"),
        ({"request": "ask", "disposition": 1}, "obligation ask: disposition is text"),
    ):
        with pytest.raises(OperationSpecError, match=message):
            CallerObligation(**fields)
    for binding, message in (
        ({"native_name": "x\x1by"}, "the native name is not in the form"),
        ({"native_name": "x", "executable": "ki\nmi"}, "the executable is not in the form"),
        ({"native_name": "x", "fixed": ("a b",)}, "each of the fixed words is not in the form"),
        ({"native_name": "x", "fixed": ["a"]}, "the fixed words is a list"),
        ({"native_name": "x", "method": "m\x00"}, "the method is not in the form"),
    ):
        with pytest.raises(OperationSpecError, match=message):
            NativeBinding(**binding)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"name": "Cwd", "type": "string"}, "argument name is lower-case"),
        ({"name": "cwd", "type": "path"}, "unknown type"),
        ({"placement": "environment"}, "argument cwd: unknown placement"),
        ({"placement": 3}, "argument cwd: placement is text"),
        ({"native": "--c wd"}, "argument cwd: native name is not in the form"),
        ({"native": "cwd"}, "a native flag starts with a dash"),
        ({"placement": "switch"}, "a switch is one boolean"),
        ({"placement": "switch", "type": "boolean", "max_items": 2}, "a switch is one boolean"),
        ({"max_items": 65}, "a list holds at most 64 values"),
        ({"max_items": -1}, "a list holds at most 64 values"),
        ({"max_items": True}, "a list holds at most 64 values"),
        ({"position": 0}, "a position belongs to a positional argument"),
        ({"placement": "position", "native": "<cwd>"}, "a position belongs to a positional"),
        ({"position": -2}, "position is a whole number"),
        ({"position": "first"}, "position is a whole number"),
        ({"minimum": 1}, "bounds belong to an integer"),
        ({"type": "integer", "minimum": 5, "maximum": 4}, "the lower bound is above"),
        ({"type": "integer", "maximum": 2.5}, "a bound is a whole number"),
        ({"type": "integer", "minimum": True}, "a bound is a whole number"),
    ],
)
def test_an_argument_binding_that_could_not_place_a_value_is_refused(
    fields: dict[str, Any], message: str
) -> None:
    base: dict[str, Any] = {"name": "cwd", "type": "string", "placement": "flag", "native": "--cwd"}
    with pytest.raises(OperationSpecError, match=message):
        ArgumentBinding(**{**base, **fields})


def _position(name: str, position: int, **more: Any) -> ArgumentBinding:
    return ArgumentBinding(name, "string", "position", f"<{name}>", position=position, **more)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            (
                ArgumentBinding("a", "string", "flag", "--a"),
                ArgumentBinding("a", "string", "flag", "--b"),
            ),
            "each argument and each native name once",
        ),
        (
            (
                ArgumentBinding("a", "string", "flag", "--a"),
                ArgumentBinding("b", "string", "flag", "--a"),
            ),
            "each argument and each native name once",
        ),
        ((ArgumentBinding("j", "boolean", "switch", "--json"),), "fixed or reserved native name"),
        ((ArgumentBinding("c", "string", "flag", "--config"),), "fixed or reserved native name"),
        ((ArgumentBinding("p", "string", "parameter", "cwd"),), "parameters belong to a request"),
        ((_position("a", 1),), "positions count from zero without a gap"),
        ((_position("a", 0), _position("b", 2)), "positions count from zero without a gap"),
        ((_position("a", 0), _position("b", 0)), "positions count from zero without a gap"),
        ((_position("a", 0, required=False), _position("b", 1)), "a required position precedes"),
        ((_position("a", 0, max_items=2), _position("b", 1)), "a list comes last"),
    ],
)
def test_arguments_that_could_be_read_in_two_ways_are_refused(
    arguments: tuple[ArgumentBinding, ...], message: str
) -> None:
    with pytest.raises(OperationSpecError, match=message):
        list_sessions(arguments=arguments)
    with pytest.raises(OperationSpecError, match="parameters belong to a request row"):
        read_session(arguments=(ArgumentBinding("cwd", "string", "flag", "--cwd"),))


def test_the_command_line_is_built_from_the_row_alone() -> None:
    row = list_sessions()

    assert row.argv({}) == ("/opt/kimi/bin/kimi", "session", "list", "--json")
    assert row.argv({"limit": "20", "all": "true", "cwd": "/srv/work"}) == (
        "/opt/kimi/bin/kimi",
        "session",
        "list",
        "--json",
        "--cwd",
        "/srv/work",
        "--all",
        "--limit",
        "20",
    )
    # A switch that is false adds nothing; it is still not the same as absent.
    assert row.argv({"all": "false"}) == row.argv({})
    assert row.arguments_from({"all": "false"}) == {"all": False} != row.arguments_from({})

    export = list_sessions(
        operation="session.export",
        binding=NativeBinding("export", executable="grok", fixed=("export",)),
        arguments=(
            _position("session", 0),
            _position("turns", 1, required=False, max_items=3),
            ArgumentBinding("tags", "string", "flag", "--tag", False, max_items=2),
            ArgumentBinding("sanitize", "boolean", "flag", "--sanitize", False),
        ),
        reserved_natives=(),
    )
    assert export.argv({"turns": ["1", "2"], "session": "s1", "tags": ["a", "b"]}) == (
        "grok",
        "export",
        "--tag",
        "a",
        "--tag",
        "b",
        "s1",
        "1",
        "2",
    )
    assert export.argv({"session": "s1", "sanitize": "false"})[-3:] == ("--sanitize", "false", "s1")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"cwd": "--config=/etc/x"}, "argument cwd: a value is not empty and begins with no dash"),
        ({"cwd": ""}, "argument cwd: a value is not empty"),
        ({"limit": "0"}, "argument limit: outside its bounds"),
        ({"limit": "501"}, "argument limit: outside its bounds"),
        ({"limit": "many"}, "argument limit: not a valid integer"),
        ({"limit": 20}, "argument limit: a value is text of bounded length"),
        ({"all": "yes"}, "argument all: not a valid boolean"),
        ({"cwd": ["/a"]}, "argument cwd: a value is text of bounded length"),
        ({"config": "x"}, "not an argument of session.list: config"),
    ],
)
def test_a_value_that_could_change_the_invocation_is_refused(
    raw: dict[str, Any], message: str
) -> None:
    with pytest.raises(OperationSpecError, match=message):
        list_sessions().argv(raw)


def test_lists_are_bounded_and_a_required_argument_is_required() -> None:
    row = list_sessions(
        arguments=(
            _position("session", 0),
            ArgumentBinding("tags", "string", "flag", "--tag", False, max_items=2),
        ),
        reserved_natives=(),
    )

    for raw, message in (
        ({}, "session.list needs the argument session"),
        ({"session": "s", "tags": ["a", "b", "c"]}, "argument tags: a list of at most 2 values"),
        ({"session": "s", "tags": "a"}, "argument tags: a list of at most 2 values"),
        ({"session": "s", "tags": []}, "argument tags: a value is not empty"),
        ({"session": "s", "tags": ["a", 5]}, "argument tags: a value is text"),
        ({"session": "s", "tags": ["a", "-b"]}, "begins with no dash"),
    ):
        with pytest.raises(OperationSpecError, match=message):
            row.argv(raw)
    assert row.argv({"session": "s", "tags": ("a",)})[-3:] == ("--tag", "a", "s")
    with pytest.raises(OperationSpecError, match="session.list is not a request row"):
        row.parameters({"session": "s"})


def test_request_parameters_carry_native_names_and_keep_absent_apart_from_false() -> None:
    row = read_session()

    assert row.parameters({"session": SESSION}) == {"threadId": SESSION}
    assert row.parameters({"session": SESSION, "include_turns": "false", "sources": []}) == {
        "threadId": SESSION,
        "includeTurns": False,
        "sourceKinds": [],
    }
    assert row.parameters({"session": SESSION, "sources": ["cli", "-x"]})["sourceKinds"] == [
        "cli",
        "-x",
    ]
    with pytest.raises(OperationSpecError, match="argument session: not a valid uuid"):
        row.parameters({"session": "night-shift"})
    with pytest.raises(OperationSpecError, match="session.read is not a command-line row"):
        row.argv({"session": SESSION})


def test_a_row_survives_the_trip_through_plain_data() -> None:
    rows = (
        qualified(list_sessions(side_effects=("none",), needs_restart=True)),
        read_session(
            effects=("read", "approve"),
            obligations=(CallerObligation("ask", "answer_with_consent"),),
            output_projection="redacted-v1",
        ),
    )

    for row in rows:
        data = json.loads(json.dumps(row.to_json()))
        assert OperationRow.from_json(data) == row
        # The plain data is plain before any encoding too: lists, not tuples.
        assert row.to_json() == data
        assert OperationRow.from_json(data).binding_digest == row.binding_digest
    data = rows[0].to_json()
    assert data["binding"] == {
        "native_name": "session list",
        "executable": "/opt/kimi/bin/kimi",
        "fixed": ["session", "list", "--json"],
        "method": "",
    }
    assert data["arguments"][2] == {
        "name": "limit",
        "type": "integer",
        "placement": "flag",
        "native": "--limit",
        "required": False,
        "choices": [],
        "description": "",
        "position": -1,
        "max_items": 0,
        "minimum": 1,
        "maximum": 500,
    }
    optional = {"arguments", "obligations", "effects", "reserved_natives", "disclosures"}
    minimal = {name: value for name, value in data.items() if name not in optional}
    with pytest.raises(OperationSpecError, match="at least one effect"):
        OperationRow.from_json(minimal)


@pytest.mark.parametrize(
    "damage",
    [
        lambda data: [data],
        lambda data: {**data, "native_name": "session list"},
        lambda data: {**data, "required_authority": "ops.read"},
        lambda data: {name: value for name, value in data.items() if name != "vendor"},
        lambda data: {name: value for name, value in data.items() if name != "binding"},
        lambda data: {**data, "binding": "kimi session list"},
        lambda data: {**data, "binding": {**data["binding"], "fixed": "session list"}},
        lambda data: {**data, "binding": {**data["binding"], "shell": True}},
        lambda data: {**data, "arguments": {"cwd": "string"}},
        lambda data: {**data, "arguments": ["cwd"]},
        lambda data: {**data, "arguments": [{**data["arguments"][0], "choices": "a"}]},
        lambda data: {**data, "arguments": [{"name": "cwd"}]},
        lambda data: {**data, "obligations": [{"request": "ask"}]},
        lambda data: {**data, "effects": "read"},
        lambda data: {**data, "side_effects": 3},
        lambda data: {**data, "scope": "world"},
    ],
)
def test_plain_data_that_is_not_a_row_is_refused(damage: Any) -> None:
    with pytest.raises(OperationSpecError):
        OperationRow.from_json(damage(list_sessions().to_json()))
