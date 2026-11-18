# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a component's own state: its observing read, the hub path and its verifier
"""The observing read, ``ops run --hub`` for a component's state, ``verify-result component``."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.core.authorisation_intent import compose_intent, parse_intent_statement
from synapse_channel.core.protocol import MessageType
from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.component_state import (
    COMPONENT_VERIFIER,
    expected_digest,
    observe,
    plan_observer,
    plan_read,
    verify_component,
)
from synapse_channel.operations.hub_intent import component_hub_intent, component_target
from synapse_channel.operations.projection import ProjectionError, projection_digest
from synapse_channel.operations.runner import (
    OperationRunRefused,
    RunProfile,
    fingerprint_executable,
    plan,
)
from test_cli_ops_hub import DEADLINE, ScriptedHub
from test_operations_runner import World, _qualified

PERSONAL = RunProfile("personal")
SUBJECT = "PROJECT/seat"


class ObservedWorld(World):
    """The fake vendor, with its setting write observed by its setting read and a mode set."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(tmp_path, monkeypatch)
        _path, fingerprint = fingerprint_executable("vendorctl")
        rows = []
        for row in self.catalogue.rows():
            if row.operation == "setting.write":
                row = replace(row, observed_by="setting.read")
            elif row.operation == "setting.read":
                row = replace(row, output_projection="mode-v1", projection_paths=("mode",))
            rows.append(_qualified(row, fingerprint))
        self.catalogue = Catalogue(rows)
        self.catalogue_file.write_text(json.dumps(self.catalogue.to_json()), encoding="utf-8")
        self.set_mode("lax")
        self.expected = tmp_path / "expected.json"
        self.expected.write_text(json.dumps({"mode": "strict"}), encoding="utf-8")

    def set_mode(self, mode: str | None) -> None:
        """Write the vendor's configuration with ``mode``, or with no mode at all."""
        document = {} if mode is None else {"mode": mode}
        self.config.write_text(json.dumps(document) + "\n", encoding="utf-8")

    def change_plan(self) -> Any:
        return plan(
            self.catalogue,
            self.target("setting.write"),
            {"key": "mode", "value": "strict"},
            profile=PERSONAL,
        )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservedWorld:
    return ObservedWorld(tmp_path, monkeypatch)


def _observe(world: ObservedWorld, run_id: str) -> str:
    read = plan_observer(world.catalogue, world.change_plan().row, PERSONAL)
    return observe(
        read,
        operation_id=run_id,
        subject=SUBJECT,
        grants_path=world.grants,
        journal=world.journal,
        timeout=30.0,
    )


def test_the_read_observes_the_projection_and_refuses_a_state_without_it(
    world: ObservedWorld,
) -> None:
    assert _observe(world, "read-1") == projection_digest({"mode": "lax"})
    world.set_mode("strict")
    assert _observe(world, "read-2") == projection_digest({"mode": "strict"})
    world.set_mode(None)
    with pytest.raises(ProjectionError, match="mode: the output has no mode"):
        _observe(world, "read-3")
    assert world.invocations().count("setting show --json") == 3


def test_an_observer_is_planned_only_for_a_row_that_names_one_and_may_observe(
    world: ObservedWorld,
) -> None:
    session_list = world.catalogue.find(
        "session.list",
        host="workstation",
        vendor="acme",
        component="cli",
        component_version="1.0.0",
        os="linux",
        transport="cli",
    )
    assert session_list is not None
    with pytest.raises(OperationRunRefused, match="session.list names no observing read row"):
        plan_observer(world.catalogue, session_list, PERSONAL)
    with pytest.raises(
        OperationRunRefused, match="cannot observe a change: it prints no projected JSON"
    ):
        plan_read(world.catalogue, world.target("session.list"), PERSONAL)


def test_a_read_that_fails_gives_no_projection(world: ObservedWorld) -> None:
    read = plan_read(world.catalogue, world.target("setting.read"), PERSONAL)
    broken = replace(read, argv=(read.argv[0], "unknown", "verb"))
    with pytest.raises(ProjectionError, match="the read setting.read ended confirmed_failed"):
        observe(
            broken,
            operation_id="read-x",
            subject=SUBJECT,
            grants_path=world.grants,
            journal=world.journal,
            timeout=30.0,
        )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ('{"mode": "strict", "extra": 1}', "names exactly the paths of setting.read: mode"),
        ("[]", "names exactly the paths of setting.read"),
        ("not json", "is not one JSON object"),
        (b"\xff\xfe", "is not one JSON object"),
        ('{"mode": 1.5}', "is outside canonical JSON"),
        ("{" + " " * (1 << 20) + "}", "larger than 1 MiB"),
    ],
)
def test_an_expected_projection_names_exactly_the_read_rows_paths(
    world: ObservedWorld, content: str | bytes, reason: str
) -> None:
    read = plan_read(world.catalogue, world.target("setting.read"), PERSONAL).row
    assert expected_digest(world.expected, read) == projection_digest({"mode": "strict"})
    path = world.expected.with_name("bad.json")
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    with pytest.raises(OperationRunRefused, match=reason):
        expected_digest(path, read)


def test_the_verifier_compares_digests_and_says_why_it_could_not(world: ObservedWorld) -> None:
    read = plan_read(world.catalogue, world.target("setting.read"), PERSONAL)
    strict = projection_digest({"mode": "strict"})

    def verify(read_id: str, **changes: Any) -> Any:
        options: dict[str, Any] = {
            "operation_id": "op-1",
            "communication": "confirmed_done",
            "read_id": read_id,
            "subject": SUBJECT,
            "grants_path": world.grants,
            "journal": world.journal,
            "timeout": 30.0,
        }
        options.update(changes)
        return verify_component(read, strict, **options)

    differs = verify("verify-1")
    assert (differs.verifier, differs.effect, differs.result) == (
        COMPONENT_VERIFIER,
        "observed",
        "not_verified",
    )
    assert differs.observation == "projection_differs"
    assert differs.evidence == {
        "read_operation": "setting.read",
        "row_fingerprint": read.fingerprint,
        "observed_sha256": projection_digest({"mode": "lax"}),
    }
    world.set_mode("strict")
    matches = verify("verify-2")
    assert (matches.result, matches.observation) == ("verified", "projection_matches")
    assert "strict" not in json.dumps(matches.to_json())
    refused = verify("verify-2")  # the id was used: the read is refused
    assert (refused.effect, refused.result, refused.observation) == (
        "unknown",
        "not_checked",
        "read_refused",
    )
    assert verify("bad id!").observation == "read_refused"
    world.set_mode(None)
    unprojected = verify("verify-3")
    assert (unprojected.result, unprojected.observation) == ("not_checked", "read_not_projected")
    assert "observed_sha256" not in unprojected.evidence


def test_the_intent_names_the_component_its_observer_and_both_digests(
    world: ObservedWorld,
) -> None:
    run = world.change_plan()
    before, after = projection_digest({"mode": "lax"}), projection_digest({"mode": "strict"})
    intent = component_hub_intent(run, before=before, expected_sha256=after, deadline=DEADLINE)
    assert intent.effect == "component_state"
    assert intent.effect_target == component_target(run.row, after)
    assert intent.effect_target == {
        "host": "workstation",
        "vendor": "acme",
        "component": "cli",
        "component_version": "1.0.0",
        "os": "linux",
        "transport": "cli",
        "observed_by": "setting.read",
        "expected_sha256": after,
    }
    assert intent.statement["pre_state"] == before
    read_only = plan(world.catalogue, world.target("setting.read"), {}, profile=PERSONAL)
    with pytest.raises(OperationRunRefused, match="changes nothing for the hub to govern"):
        component_hub_intent(read_only, before=before, expected_sha256=after, deadline=DEADLINE)


def _command(world: ObservedWorld, *extra: str) -> list[str]:
    """Return the observed setting write of the fake vendor through the scripted hub."""
    return [
        "ops", "run", "setting.write",
        "--host", "workstation", "--vendor", "acme", "--component", "cli",
        "--component-version", "1.0.0", "--os", "linux",
        "--arguments", json.dumps({"key": "mode", "value": "strict"}),
        "--operation-id", "op-1", "--subject", SUBJECT,
        "--grants", str(world.grants), "--profile", "personal",
        "--journal", str(world.journal), "--catalogue", str(world.catalogue_file),
        *extra,
    ]  # fmt: skip


def _hub(*extra: str) -> list[str]:
    return [
        "--hub", "ws://scripted", "--task", "task-1", "--epoch", "3",
        "--intent-deadline", str(DEADLINE), *extra,
    ]  # fmt: skip


def _digest(world: ObservedWorld, before: str) -> str:
    """Return the digest the hub composes for the observed write, as an approver sees it."""
    intent = component_hub_intent(
        world.change_plan(),
        before=before,
        expected_sha256=projection_digest({"mode": "strict"}),
        deadline=DEADLINE,
    )
    return compose_intent(
        parse_intent_statement(intent.statement, "component_state"),
        task_id="task-1",
        operation_id="op-1",
        effect="component_state",
        target=intent.effect_target,
        actor=SUBJECT,
    ).sha256


def _line(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, lines
    result: dict[str, Any] = json.loads(lines[0])
    return result


def test_declare_runs_only_the_read_and_names_the_component_state(
    world: ObservedWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = ScriptedHub(monkeypatch)
    hub.answers.append(
        {
            "type": MessageType.OPERATION_RECORD_RESULT,
            "status": "accepted",
            "intent_sha256": "d" * 64,
        }
    )
    argv = _command(world, *_hub("--declare", "--expect-projection", str(world.expected)))
    assert main(argv) == 0
    assert _line(capsys)["status"] == "accepted"
    ((_actor, kind, fields),) = hub.requests
    assert (kind, fields["effect"]) == (MessageType.OPERATION_RECORD, "component_state")
    assert fields["effect_target"]["expected_sha256"] == projection_digest({"mode": "strict"})
    assert fields["intent"]["pre_state"] == projection_digest({"mode": "lax"})
    assert world.invocations() == ["setting show --json"]  # the change did not run


def test_the_hub_path_refuses_a_component_run_without_what_it_needs(
    world: ObservedWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = ScriptedHub(monkeypatch)
    assert main(_command(world, *_hub("--declare"))) == 2
    assert "needs --expect-projection and --intent-deadline" in capsys.readouterr().err
    expect = ("--expect-projection", str(world.expected))
    assert main(_command(world, *expect)) == 2
    assert "--expect-projection needs --hub" in capsys.readouterr().err
    session = [
        "ops", "run", "session.list", "--host", "workstation", "--vendor", "acme",
        "--component", "cli", "--component-version", "1.0.0", "--os", "linux",
        "--operation-id", "op-2", "--subject", SUBJECT, "--grants", str(world.grants),
        "--profile", "personal", "--journal", str(world.journal),
        "--catalogue", str(world.catalogue_file), *_hub("--declare", *expect),
    ]  # fmt: skip
    assert main(session) == 2
    assert "belongs to a row that names observed_by" in capsys.readouterr().err
    world.set_mode(None)
    assert main(_command(world, *_hub("--declare", *expect))) == 2
    assert "the output has no mode" in capsys.readouterr().err
    assert hub.requests == []


def test_a_state_changed_since_the_declaration_is_refused_before_the_hub_is_asked(
    world: ObservedWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = ScriptedHub(monkeypatch)
    approved = _digest(world, projection_digest({"mode": "lax"}))
    world.set_mode("other")
    expect = ("--expect-projection", str(world.expected))
    assert main(_command(world, *_hub("--intent-sha256", approved, *expect))) == 2
    assert "the component's state or the identity changed" in capsys.readouterr().err
    assert hub.requests == []
    assert "setting set mode strict" not in world.invocations()


def test_an_approved_component_run_opens_its_attempt_and_runs_once(
    world: ObservedWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = ScriptedHub(monkeypatch)
    hub.answers.extend(
        [
            {
                "type": MessageType.OPERATION_ATTEMPT_RESULT,
                "answer": "approved_fresh",
                "attempt_id": "att-1",
                "live": {"attempt_state": "open"},
            },
            {"type": MessageType.OPERATION_RECORD_RESULT, "status": "accepted"},
        ]
    )
    approved = _digest(world, projection_digest({"mode": "lax"}))
    expect = ("--expect-projection", str(world.expected))
    assert main(_command(world, *_hub("--intent-sha256", approved, *expect))) == 0
    report = _line(capsys)
    assert (report["state"], report["hub_governed"]) == ("confirmed_done", True)
    assert world.invocations() == ["setting show --json", "setting set mode strict"]
    assert [kind for _actor, kind, _fields in hub.requests] == [
        MessageType.OPERATION_ATTEMPT,
        MessageType.OPERATION_RECORD,
    ]


def _verify_argv(world: ObservedWorld, expected: str, *extra: str) -> list[str]:
    return [
        "verify-result", "component", "--observed-by", "setting.read",
        "--host", "workstation", "--vendor", "acme", "--component", "cli",
        "--component-version", "1.0.0", "--os", "linux", "--expect-sha256", expected,
        "--subject", SUBJECT, "--grants", str(world.grants), "--profile", "personal",
        "--catalogue", str(world.catalogue_file), "--journal", str(world.journal),
        "--operation-id", "op-1", *extra,
    ]  # fmt: skip


def test_verify_result_component_exits_by_its_result(
    world: ObservedWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    strict = projection_digest({"mode": "strict"})
    assert main(_verify_argv(world, strict)) == 1
    assert _line(capsys)["observation"] == "projection_differs"
    world.set_mode("strict")
    assert main(_verify_argv(world, strict)) == 0
    record = _line(capsys)
    assert (record["verifier"], record["result"]) == ("component_read", "verified")
    world.set_mode(None)
    assert main(_verify_argv(world, strict)) == 3
    assert _line(capsys)["observation"] == "read_not_projected"
    assert main(_verify_argv(world, "nothex")) == 2
    assert "64 lower-case hex digits" in capsys.readouterr().err
    session = _verify_argv(world, strict)
    session[session.index("setting.read")] = "session.list"
    assert main(session) == 2
    assert "cannot observe a change" in capsys.readouterr().err


def test_verify_result_component_refuses_a_catalogue_of_the_earlier_schema(
    world: ObservedWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = world.catalogue_file.with_name("legacy.json")
    legacy.write_text(
        json.dumps({"schema": "operation-catalogue-v1", "operations": []}), encoding="utf-8"
    )
    argv = _verify_argv(world, projection_digest({"mode": "strict"}))
    argv[argv.index(str(world.catalogue_file))] = str(legacy)
    assert main(argv) == 2
    assert "the earlier schema observes nothing" in capsys.readouterr().err
