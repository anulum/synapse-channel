# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the authorisation intent of a planned catalogue run
"""Build a hub declaration's intent from a planned run, and refuse what cannot go through."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.core.authorisation_intent import compose_intent, parse_intent_statement
from synapse_channel.operations.hub_intent import HUB_PATH_EFFECTS, hub_intent
from synapse_channel.operations.row import NativeBinding, OperationRow
from synapse_channel.operations.runner import OperationRunRefused, RunPlan, RunProfile

BEFORE = "1" * 64
AFTER = "2" * 64
DEADLINE = 1_790_000_000
PATH = "/home/operator/.config/acme/config.json"


def _plan(*, effects: tuple[str, ...] = ("change",), **changes: Any) -> RunPlan:
    """Return a planned run of the fake vendor's setting write, with ``changes`` to its row."""
    fields: dict[str, Any] = {
        "host": "workstation",
        "vendor": "acme",
        "component": "cli",
        "component_version": "1.0.0",
        "os": "linux",
        "operation": "setting.write",
        "transport": "cli",
        "scope": "user",
        "effects": effects,
        "binding": NativeBinding("setting.write", executable="vendorctl", fixed=("set",)),
        "output_mode": "json",
        "idempotent": "yes",
        "version_printed": "1.0.0",
    }
    fields.update(changes)
    row = OperationRow(**fields)
    argv = ("/usr/bin/vendorctl", "set", "mode", "strict")
    return RunPlan(row, argv, argv[0], "sha256:" + "a" * 64, RunProfile("personal"), "personal")


def _intent(run: RunPlan, **changes: Any) -> dict[str, Any]:
    """Return the statement of ``run`` with the default file, states and deadline."""
    arguments: dict[str, Any] = {
        "path": PATH,
        "before": BEFORE,
        "after_sha256": AFTER,
        "deadline": DEADLINE,
    }
    arguments.update(changes)
    return hub_intent(run, **arguments).statement


def test_a_change_row_yields_a_statement_the_hub_accepts() -> None:
    run = _plan()
    intent = hub_intent(run, path=PATH, before="absent", after_sha256=AFTER, deadline=DEADLINE)

    assert intent.effect_target == {"path": PATH, "sha256": AFTER}
    statement = intent.statement
    assert parse_intent_statement(statement, "file").document == statement
    assert statement["tool"] == {"name": "acme/cli", "version": "1.0.0"}
    assert statement["tool_descriptor_sha256"] == "a" * 64
    assert statement["rule_generation"] == run.row.binding_digest
    assert (statement["pre_state"], statement["recovery_class"]) == ("absent", "reversible")
    assert (statement["budget"], statement["egress"]) == ("not_applicable", [])
    assert statement["required_assurance"] == "cooperative"
    assert _intent(_plan(effects=("read", "destroy")))["recovery_class"] == "one_way"
    assert HUB_PATH_EFFECTS == {"read", "change", "destroy"}


@pytest.mark.parametrize(
    ("effects", "reason"),
    [
        (("change", "execute"), "not one file's change"),
        (("change", "extend"), "named person's consent"),
        (("approve",), "named person's consent"),
        (("read",), "changes nothing for the hub to govern"),
    ],
)
def test_rows_that_are_not_one_files_change_are_refused(
    effects: tuple[str, ...], reason: str
) -> None:
    with pytest.raises(OperationRunRefused, match=reason):
        _intent(_plan(effects=effects))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"path": "config.json"}, "absolute file path"),
        ({"path": "/" + "x" * 512}, "absolute file path"),
        ({"path": "/home/operator/a\x00b"}, "absolute file path"),
        ({"after_sha256": "2" * 63}, "expected SHA-256"),
        ({"before": "deadbeef"}, "pre_state"),
        ({"deadline": 0}, "deadline"),
    ],
)
def test_a_malformed_file_state_or_deadline_is_refused(
    changes: dict[str, Any], reason: str
) -> None:
    with pytest.raises(OperationRunRefused, match=reason):
        _intent(_plan(), **changes)


@pytest.mark.parametrize(
    ("changed", "field"),
    [
        ({"operation": "setting.erase"}, "arguments_sha256"),
        ({"host": "laptop"}, "arguments_sha256"),
        ({"os": "macos"}, "arguments_sha256"),
        ({"variant": "portable"}, "arguments_sha256"),
        ({"component_version": "1.0.1"}, "tool"),
        (
            {"binding": NativeBinding("setting.write", executable="vendorctl", fixed=("put",))},
            "rule_generation",
        ),
    ],
)
def test_each_row_field_moves_the_statement(changed: dict[str, Any], field: str) -> None:
    assert _intent(_plan(**changed))[field] != _intent(_plan())[field]


def test_the_command_line_and_the_program_move_the_statement_but_not_its_local_path() -> None:
    run = _plan()
    statement = _intent(run)

    assert (
        _intent(replace(run, argv=(*run.argv[:-1], "lenient")))["arguments_sha256"]
        != statement["arguments_sha256"]
    )
    assert (
        _intent(replace(run, fingerprint="sha256:" + "b" * 64))["tool_descriptor_sha256"]
        == "b" * 64
    )
    moved = replace(
        run, argv=("/opt/acme/bin/vendorctl", *run.argv[1:]), executable="/opt/acme/bin/vendorctl"
    )
    assert _intent(moved) == statement


def test_the_composed_intent_moves_with_the_files_expected_content() -> None:
    """The hub adds the target; an approval of one content cannot admit another."""
    run = _plan()

    def digest(after: str) -> str:
        intent = hub_intent(run, path=PATH, before=BEFORE, after_sha256=after, deadline=DEADLINE)
        return compose_intent(
            parse_intent_statement(intent.statement, "file"),
            task_id="task-1",
            operation_id="op-1",
            effect="file",
            target=intent.effect_target,
            actor="ACME/runner",
        ).sha256

    assert digest(AFTER) != digest("3" * 64)


def test_a_command_line_that_cannot_be_written_canonically_is_refused() -> None:
    run = _plan()
    broken = replace(run, argv=(*run.argv[:-1], "\ud800"))

    with pytest.raises(OperationRunRefused, match="cannot be written canonically"):
        _intent(broken)
