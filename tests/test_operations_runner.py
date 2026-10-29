# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — catalogue operations run end to end, and refuse when they must
"""Three catalogue operations along the checked path, and the four required failures.

A real program, ``vendorctl``, stands in for a vendor client on ``PATH``. It
lists sessions, reads its settings and writes one setting to a real file,
counting every invocation. The runner reads a real catalogue, a real grants
file and a real journal. The acceptance cases the TODO requires are all here:

* authority withdrawn between planning and execution;
* the target program replaced after planning;
* a crash after the effect, by killing the real runner process;
* a lost result, through the deadline.

In every one of them the operation is not run twice. An unknown outcome is
reported as unknown, and its effect can be verified without a second run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_ops_run import _status, default_journal
from synapse_channel.operations.catalogue import Catalogue, LegacyInventory
from synapse_channel.operations.qualification_bundle import sign_bundle
from synapse_channel.operations.row import ArgumentBinding, NativeBinding, OperationRow
from synapse_channel.operations.runner import (
    MAX_OUTPUT_BYTES,
    OperationRunRefused,
    OperationTarget,
    RunPlan,
    RunProfile,
    authorised,
    execute,
    fingerprint_executable,
    inquire,
    load_grants,
    plan,
)
from synapse_channel.operations.runner_journal import (
    RunJournalError,
    read_record,
    run_directory,
    run_state,
    write_record,
)
from synapse_channel.result_verification import verify_file_change
from test_operations_spec import queue_message

ROOT = Path(__file__).resolve().parents[1]
VENDOR = """#!{python}
import json, os, subprocess, sys, time
log = os.environ["VENDOR_LOG"]
with open(log, "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
config = os.environ["VENDOR_CONFIG"]
words = sys.argv[1:]
if words[:3] == ["session", "list", "--json"]:
    print(json.dumps([{{"id": "s-1"}}, {{"id": "s-2"}}]))
elif words[:3] == ["setting", "show", "--json"]:
    print(open(config, encoding="utf-8").read().strip() if os.path.exists(config) else "{{}}")
elif words[:2] == ["setting", "set"]:
    with open(config, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({{words[2]: words[3]}}) + "\\n")
    if os.environ.get("VENDOR_LOCK_DIR"):
        os.chmod(os.environ["VENDOR_LOCK_DIR"], 0o500)
    time.sleep(float(os.environ.get("VENDOR_SLEEP_AFTER_EFFECT", "0")))
elif words[:1] == ["noise"]:
    sys.stdout.buffer.write(b"x" * (2 << 20))
    sys.stderr.buffer.write(b"e" * (2 << 20))
elif words[:1] == ["orphan"]:
    child = subprocess.Popen(["sleep", "30"])
    with open(os.environ["VENDOR_ORPHAN_PID"], "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
    print("parent")
else:
    sys.exit(4)
"""


PERSONAL = RunProfile("personal")


def plan_personal(
    catalogue: Catalogue, target: OperationTarget, arguments: dict[str, object]
) -> RunPlan:
    """Plan under the personal profile, as most cases here do."""
    return plan(catalogue, target, arguments, profile=PERSONAL)


def _row(operation: str, fixed: tuple[str, ...], **changes: Any) -> OperationRow:
    """Return a command-line row of the fake vendor, with ``changes`` applied."""
    fields: dict[str, Any] = {
        "host": "workstation",
        "vendor": "acme",
        "component": "cli",
        "component_version": "1.0.0",
        "os": "linux",
        "operation": operation,
        "transport": "cli",
        "scope": "user",
        "effects": ("read",),
        "binding": NativeBinding(operation, executable="vendorctl", fixed=fixed),
        "output_mode": "json",
        "idempotent": "yes",
        "version_printed": "1.0.0",
    }
    fields.update(changes)
    return OperationRow(**fields)


def _qualified(row: OperationRow, fingerprint: str) -> OperationRow:
    """Return ``row`` qualified for the executable with ``fingerprint``."""
    bound = replace(row, fingerprint=fingerprint)
    return replace(
        bound,
        qualification="run_once",
        evidence="tests/qualification",
        qualified_binding=bound.binding_digest,
    )


WRITE_ARGUMENTS = (
    ArgumentBinding("key", "string", "position", "<key>", position=0),
    ArgumentBinding("value", "string", "position", "<value>", position=1),
)


class World:
    """A vendor program on PATH, its catalogue, grants and journal, all real files."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Install the vendor program, its catalogue, a grant and an empty journal."""
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.program = self.bin / "vendorctl"
        self.write_program()
        self.config = tmp_path / "config.json"
        self.log = tmp_path / "invocations.log"
        self.grants = tmp_path / "grants.json"
        self.journal = tmp_path / "journal"
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("VENDOR_CONFIG", str(self.config))
        monkeypatch.setenv("VENDOR_LOG", str(self.log))
        _path, fingerprint = fingerprint_executable("vendorctl")
        self.catalogue = Catalogue(
            [
                _qualified(_row("session.list", ("session", "list", "--json")), fingerprint),
                _qualified(_row("setting.read", ("setting", "show", "--json")), fingerprint),
                _qualified(
                    _row(
                        "setting.write",
                        ("setting", "set"),
                        effects=("change",),
                        arguments=WRITE_ARGUMENTS,
                        output_mode="none",
                    ),
                    fingerprint,
                ),
                _qualified(
                    _row("session.export", ("unknown", "verb"), effects=("read",)), fingerprint
                ),
                _qualified(_row("diagnostics.read", ("noise",)), fingerprint),
                _qualified(_row("turn.list", ("orphan",)), fingerprint),
            ]
        )
        self.catalogue_file = tmp_path / "catalogue.json"
        self.catalogue_file.write_text(json.dumps(self.catalogue.to_json()), encoding="utf-8")
        self.grant(("operation-read", "operation-change"))

    def write_program(self, extra: str = "") -> None:
        """Write the vendor program, with ``extra`` appended to change its bytes."""
        self.program.write_text(VENDOR.format(python=sys.executable) + extra, encoding="utf-8")
        self.program.chmod(0o755)

    def grant(self, permissions: tuple[str, ...], *, expires_at: float | None = None) -> None:
        """Write an owner-only grants file giving ``permissions`` to the test seat."""
        grant = {
            "subject": "PROJECT/seat",
            "permissions": list(permissions),
            "component": "cli",
            "expires_at": expires_at if expires_at is not None else time.time() + 3600,
        }
        self.grants.write_text(json.dumps({"grants": [grant]}), encoding="utf-8")
        self.grants.chmod(0o600)

    def invocations(self) -> list[str]:
        """Return the argument lines the vendor program recorded, in order."""
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def target(self, operation: str) -> OperationTarget:
        """Return the exact target of ``operation`` on the fake vendor."""
        return OperationTarget(operation, "workstation", "acme", "cli", "1.0.0", "linux")

    def run(self, operation: str, arguments: dict[str, object] | None = None, **kw: Any) -> Any:
        """Plan ``operation`` under the personal profile and execute it."""
        prepared = plan_personal(self.catalogue, self.target(operation), arguments or {})
        kw.setdefault("timeout", 30.0)
        return execute(
            prepared,
            operation_id=kw.pop("operation_id", operation.replace(".", "-")),
            subject="PROJECT/seat",
            grants_path=self.grants,
            journal=kw.pop("journal", self.journal),
            **kw,
        )

    def written(self, value: str) -> str:
        """Return the SHA-256 of the settings file holding ``mode: value``."""
        return hashlib.sha256((json.dumps({"mode": value}) + "\n").encode()).hexdigest()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """Provide a fresh vendor world per test."""
    return World(tmp_path, monkeypatch)


def test_session_discovery_and_effective_settings_run_end_to_end(world: World) -> None:
    """Two read operations run once each and return the vendor's output."""
    sessions = world.run("session.list")
    settings = world.run("setting.read")

    assert (sessions.state, sessions.returncode) == ("confirmed_done", 0)
    assert json.loads(sessions.output) == [{"id": "s-1"}, {"id": "s-2"}]
    assert json.loads(settings.output) == {}
    assert sessions.to_json()["external_effect"] == "unknown"
    assert inquire(world.journal, "session-list").state == "confirmed_done"
    assert world.invocations() == ["session list --json", "setting show --json"]


def test_a_controlled_mutation_is_verified_by_its_effect(world: World) -> None:
    """A write is confirmed and its file effect verified; the start names the subject."""

    def verifier(operation_id: str, state: str) -> Any:
        """Check the settings file holds ``mode: strict``."""
        return verify_file_change(
            world.config, world.written("strict"), operation_id=operation_id, communication=state
        )

    report = world.run("setting.write", {"key": "mode", "value": "strict"}, verifier=verifier)

    assert report.state == "confirmed_done"
    assert report.verification is not None and report.verification.verified
    assert report.to_json()["external_effect"] == "observed"
    assert world.invocations() == ["setting set mode strict"]
    start = read_record(run_directory(world.journal, "setting-write"), "start.json")
    assert start is not None and start["subject"] == "PROJECT/seat"


def test_authority_withdrawn_between_planning_and_execution_refuses(world: World) -> None:
    """A grant withdrawn after planning refuses the run before any invocation."""
    prepared = plan_personal(
        world.catalogue, world.target("setting.write"), {"key": "mode", "value": "x"}
    )
    world.grant(("operation-read",))

    with pytest.raises(OperationRunRefused, match="holds no current grant"):
        execute(
            prepared,
            operation_id="withdrawn",
            subject="PROJECT/seat",
            grants_path=world.grants,
            journal=world.journal,
            timeout=30.0,
        )
    assert world.invocations() == []
    assert run_state(world.journal, "withdrawn") == ("absent", None)


def test_an_expired_or_foreign_grant_is_no_authority(world: World) -> None:
    """Expired and foreign grants give no authority; a wildcard component does."""
    row = world.catalogue.rows(host=None, vendor=None, transport=None)[0]
    now = time.time()
    expired = [
        {
            "subject": "PROJECT/seat",
            "permissions": ["operation-read"],
            "component": "cli",
            "expires_at": now - 1,
        }
    ]
    foreign = [
        {
            "subject": "PROJECT/other",
            "permissions": ["operation-read"],
            "component": "*",
            "expires_at": now + 9,
        }
    ]
    wildcard = [
        {
            "subject": "PROJECT/seat",
            "permissions": ["operation-read"],
            "component": "*",
            "expires_at": now + 9,
        }
    ]

    assert not authorised(expired, subject="PROJECT/seat", row=row, now=now)
    assert not authorised(foreign, subject="PROJECT/seat", row=row, now=now)
    assert authorised(wildcard, subject="PROJECT/seat", row=row, now=now)


def test_a_program_replaced_after_planning_refuses(world: World) -> None:
    """A replaced vendor program refuses the run; an unreadable one cannot be fingerprinted."""
    prepared = plan_personal(
        world.catalogue, world.target("setting.write"), {"key": "mode", "value": "x"}
    )
    world.write_program("# replaced\n")

    with pytest.raises(OperationRunRefused, match="program was replaced"):
        execute(
            prepared,
            operation_id="replaced",
            subject="PROJECT/seat",
            grants_path=world.grants,
            journal=world.journal,
            timeout=30.0,
        )
    assert world.invocations() == []
    with pytest.raises(OperationRunRefused, match="another executable"):
        plan_personal(world.catalogue, world.target("setting.write"), {"key": "mode", "value": "x"})
    world.program.chmod(0o111)
    try:
        with pytest.raises(OperationRunRefused, match="cannot be read"):
            fingerprint_executable("vendorctl")
    finally:
        world.program.chmod(0o755)


def test_a_crash_after_the_effect_is_unknown_never_rerun_and_verifiable(
    world: World, tmp_path: Path
) -> None:
    """Kill the real runner while the component is past its effect."""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "VENDOR_SLEEP_AFTER_EFFECT": "30"}
    command = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "ops",
        "run",
        "setting.write",
        "--host",
        "workstation",
        "--vendor",
        "acme",
        "--component",
        "cli",
        "--component-version",
        "1.0.0",
        "--os",
        "linux",
        "--arguments",
        json.dumps({"key": "mode", "value": "crash"}),
        "--operation-id",
        "crashed",
        "--subject",
        "PROJECT/seat",
        "--grants",
        str(world.grants),
        "--profile",
        "personal",
        "--journal",
        str(world.journal),
        "--catalogue",
        str(world.catalogue_file),
    ]
    runner = subprocess.Popen(command, env=env, start_new_session=True)
    deadline = time.monotonic() + 30
    while not world.config.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.killpg(runner.pid, signal.SIGKILL)
    runner.wait(10)

    report = inquire(world.journal, "crashed")
    assert (report.state, report.observation) == ("unknown", "start_without_outcome")
    with pytest.raises(OperationRunRefused, match="already started"):
        world.run("setting.write", {"key": "mode", "value": "crash"}, operation_id="crashed")
    verified = inquire(
        world.journal,
        "crashed",
        verifier=lambda op, state: verify_file_change(
            world.config, world.written("crash"), operation_id=op, communication=state
        ),
    )
    assert verified.verification is not None and verified.verification.verified
    assert world.invocations() == ["setting set mode crash"]


def test_a_lost_result_after_the_deadline_is_unknown_and_verifiable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A component cut off by its deadline is unknown, and its effect can be verified."""
    monkeypatch.setenv("VENDOR_SLEEP_AFTER_EFFECT", "5")

    report = world.run(
        "setting.write",
        {"key": "mode", "value": "late"},
        timeout=1.0,
        verifier=lambda op, state: verify_file_change(
            world.config, world.written("late"), operation_id=op, communication=state
        ),
    )

    assert (report.state, report.observation) == ("unknown", "deadline_passed")
    assert report.verification is not None and report.verification.verified
    assert inquire(world.journal, "setting-write").observation == "deadline_passed"


def test_a_failing_component_and_an_unstartable_one_are_recorded(world: World) -> None:
    """A nonzero exit is confirmed failed; a program that cannot start is not started."""
    failed = world.run("session.export")
    world.program.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    world.program.chmod(0o755)
    _path, fingerprint = fingerprint_executable("vendorctl")
    broken = Catalogue(
        [_qualified(_row("session.list", ("session", "list", "--json")), fingerprint)]
    )
    unstartable = execute(
        plan_personal(broken, world.target("session.list"), {}),
        operation_id="unstartable",
        subject="PROJECT/seat",
        grants_path=world.grants,
        journal=world.journal,
        timeout=30.0,
    )

    assert (failed.state, failed.returncode) == ("confirmed_failed", 4)
    assert (unstartable.state, unstartable.observation) == (
        "not_started",
        "component_could_not_start",
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"effects": ("extend",)}, "needs a person's authority"),
        (
            {"disclosures": ("credential_bearing",), "output_projection": "redacted-v1"},
            "can print credentials",
        ),
        (
            {"binding": NativeBinding("x", executable="no-such-vendorctl", fixed=("x",))},
            "is not found",
        ),
    ],
)
def test_rows_the_runner_never_runs_are_refused_at_planning(
    world: World, changes: dict[str, Any], message: str
) -> None:
    """Rows needing a person, printing credentials or naming no program are refused."""
    row = _row("session.list", ("session", "list", "--json"), **changes)
    _path, fingerprint = fingerprint_executable("vendorctl")
    catalogue = Catalogue([_qualified(row, fingerprint)])

    with pytest.raises(OperationRunRefused, match=message):
        plan_personal(catalogue, world.target("session.list"), {})


def test_planning_refuses_an_absent_row_and_refused_arguments(world: World) -> None:
    """An unknown operation and arguments the row refuses are refused at planning."""
    with pytest.raises(OperationRunRefused, match="no command-line row"):
        plan_personal(world.catalogue, world.target("session.search"), {})
    with pytest.raises(OperationRunRefused):
        plan_personal(world.catalogue, world.target("setting.write"), {"key": "-x", "value": "y"})


def test_unsafe_or_malformed_grants_and_bad_deadlines_refuse(world: World) -> None:
    """Unsafe, malformed or missing grants and a zero deadline are refused."""
    prepared = plan_personal(world.catalogue, world.target("session.list"), {})
    world.grants.chmod(0o666)
    with pytest.raises(OperationRunRefused, match="writable by no one else"):
        load_grants(world.grants)
    world.grants.write_text("[]", encoding="utf-8")
    world.grants.chmod(0o600)
    with pytest.raises(OperationRunRefused, match="is not"):
        load_grants(world.grants)
    world.grants.write_text(json.dumps({"grants": ["x"]}), encoding="utf-8")
    with pytest.raises(OperationRunRefused, match="is not"):
        load_grants(world.grants)
    with pytest.raises(OperationRunRefused, match="cannot be read"):
        load_grants(world.grants.parent / "missing.json")
    with pytest.raises(OperationRunRefused, match="deadline"):
        execute(
            prepared,
            operation_id="bad-deadline",
            subject="PROJECT/seat",
            grants_path=world.grants,
            journal=world.journal,
            timeout=0.0,
        )


def test_the_journal_refuses_bad_ids_damage_and_rewrites(tmp_path: Path) -> None:
    """The journal refuses bad ids, damaged records, rewrites and lone outcomes."""
    with pytest.raises(RunJournalError, match="operation id"):
        run_directory(tmp_path, "../escape")
    directory = run_directory(tmp_path, "op-1")
    write_record(directory, "start.json", {"operation_id": "op-1"})
    with pytest.raises(FileExistsError):
        write_record(directory, "start.json", {"operation_id": "op-1"})
    (directory / "outcome.json").write_text("{", encoding="utf-8")
    with pytest.raises(RunJournalError, match="cannot be read"):
        run_state(tmp_path, "op-1")
    (directory / "outcome.json").write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(RunJournalError, match="is not a"):
        run_state(tmp_path, "op-1")
    lone = run_directory(tmp_path, "op-2")
    write_record(lone, "outcome.json", {"state": "confirmed_done"})
    with pytest.raises(RunJournalError, match="not a known run outcome"):
        run_state(tmp_path, "op-2")
    assert inquire(tmp_path, "op-3").state == "absent"


def test_a_start_record_written_by_another_runner_wins(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runners racing the same id: the second loses at the no-replace link."""
    prepared = plan_personal(world.catalogue, world.target("session.list"), {})

    def race(journal: Path, operation_id: str) -> Any:
        """Report the state, then let another runner write the start first."""
        state = run_state(journal, operation_id)
        write_record(run_directory(journal, operation_id), "start.json", {"operation_id": "x"})
        return state

    monkeypatch.setattr("synapse_channel.operations.runner.run_state", race)
    with pytest.raises(OperationRunRefused, match="already started"):
        execute(
            prepared,
            operation_id="raced",
            subject="PROJECT/seat",
            grants_path=world.grants,
            journal=world.journal,
            timeout=30.0,
        )
    assert world.invocations() == []


def test_the_cli_runs_inquires_and_reports_by_exit_status(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI maps each run and inquiry result to its documented exit status."""
    base = [
        "ops",
        "run",
        "--host",
        "workstation",
        "--vendor",
        "acme",
        "--component",
        "cli",
        "--component-version",
        "1.0.0",
        "--os",
        "linux",
        "--subject",
        "PROJECT/seat",
        "--grants",
        str(world.grants),
        "--profile",
        "personal",
        "--journal",
        str(world.journal),
        "--catalogue",
        str(world.catalogue_file),
    ]
    assert main([*base, "session.list", "--operation-id", "c1"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "confirmed_done"
    write = [*base, "setting.write", "--arguments", json.dumps({"key": "mode", "value": "cli"})]
    good = ["--verify-file", str(world.config), "--verify-sha256", world.written("cli")]
    assert main([*write, "--operation-id", "c2", *good]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                *write,
                "--operation-id",
                "c3",
                "--verify-file",
                str(world.config),
                "--verify-sha256",
                "0" * 64,
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert main([*base, "session.export", "--operation-id", "c4"]) == 1
    capsys.readouterr()
    assert (
        main(["ops", "inquire", "--operation-id", "c2", "--journal", str(world.journal), *good])
        == 0
    )
    capsys.readouterr()
    assert main([*base, "session.list", "--operation-id", "c1"]) == 2
    assert "already started" in capsys.readouterr().err
    assert main([*base, "session.list", "--operation-id", "c5", "--arguments", "[]"]) == 2
    assert main([*base, "session.list", "--operation-id", "c6", "--verify-file", "x"]) == 2
    assert main(["ops", "inquire", "--operation-id", "c2", "--verify-file", "x"]) == 2
    assert (
        main(["ops", "inquire", "--operation-id", "../bad", "--journal", str(world.journal)]) == 2
    )
    capsys.readouterr()

    directory = run_directory(world.journal, "c7")
    write_record(directory, "start.json", {"operation_id": "c7"})
    assert main(["ops", "inquire", "--operation-id", "c7", "--journal", str(world.journal)]) == 3
    assert json.loads(capsys.readouterr().out)["observation"] == "start_without_outcome"
    assert (
        main(
            [
                "ops",
                "inquire",
                "--operation-id",
                "c7",
                "--journal",
                str(world.journal),
                "--verify-file",
                str(world.config),
                "--verify-sha256",
                world.written("cli"),
            ]
        )
        == 3
    )
    capsys.readouterr()
    missing = world.journal.parent / "absent.json"
    assert (
        main(
            [
                "ops",
                "inquire",
                "--operation-id",
                "c2",
                "--journal",
                str(world.journal),
                "--verify-file",
                str(missing),
                "--verify-sha256",
                world.written("cli"),
            ]
        )
        == 1
    )

    legacy = world.journal.parent / "legacy.json"
    legacy.write_text(json.dumps(LegacyInventory([queue_message()]).to_json()), "utf-8")
    assert (
        main([*base[:-2], "--catalogue", str(legacy), "session.list", "--operation-id", "c8"]) == 2
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(world.journal.parent / "xdg"))
    assert default_journal() == world.journal.parent / "xdg" / "synapse" / "operations" / "runs"


def _production(world: World, *, valid_for: int = 3600) -> tuple[RunProfile, Path]:
    """Sign a bundle for session.list with a fresh key; return the profile and trust file."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.generate()
    rows = world.catalogue.rows(host=None, vendor=None, transport=None)
    row = next(row for row in rows if row.operation == "session.list")
    now = int(time.time())
    body = {
        "schema": "synapse-qualification-bundle.v1",
        "operation": row.operation,
        "transport": row.transport,
        "variant": row.variant,
        "binding_digest": row.binding_digest,
        "level": "run_once",
        "tests": ["tests/test_operations_runner.py"],
        "configuration_sha256": "sha256:" + "1" * 64,
        "artefacts": {"vendorctl": "sha256:" + "2" * 64},
        "environment": {"python": sys.version.split()[0]},
        "qualifier": "QA/seat",
        "issued_at": now - 10,
        "valid_until": now + valid_for,
    }
    envelope = sign_bundle(body, key, "qa-1")
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust = world.grants.parent / "trust.json"
    trust.write_text(json.dumps({"keys": {"qa-1": base64.b64encode(public).decode()}}), "utf-8")
    bundle = world.grants.parent / "bundle.json"
    bundle.write_text(json.dumps(envelope), "utf-8")
    return RunProfile("production", envelope, {"qa-1": public}, ("python",)), trust


def test_production_runs_only_on_a_signed_current_bundle(world: World) -> None:
    """Production runs with a trusted, matching bundle and refuses without one."""
    profile, _trust = _production(world)
    target = world.target("session.list")

    prepared = plan(world.catalogue, target, {}, profile=profile)
    report = execute(
        prepared,
        operation_id="prod-1",
        subject="PROJECT/seat",
        grants_path=world.grants,
        journal=world.journal,
        timeout=30.0,
    )
    start = read_record(run_directory(world.journal, "prod-1"), "start.json")

    assert report.state == "confirmed_done"
    assert start is not None and start["profile"] == "production"
    assert start["basis"] == "signed_qualification_bundle by QA/seat"
    with pytest.raises(OperationRunRefused, match="needs a signed qualification bundle"):
        plan(world.catalogue, target, {}, profile=RunProfile("production"))
    with pytest.raises(OperationRunRefused, match="does not cover the environment"):
        plan(
            world.catalogue,
            target,
            {},
            profile=replace(profile, required_environment=("python", "plugin")),
        )
    with pytest.raises(OperationRunRefused, match="a profile is one of"):
        plan(world.catalogue, target, {}, profile=RunProfile("lab"))


def test_a_bundle_that_expires_after_planning_refuses_the_run(world: World) -> None:
    """A bundle that expires between planning and execution refuses the run."""
    profile, _trust = _production(world, valid_for=60)
    prepared = plan(world.catalogue, world.target("session.list"), {}, profile=profile)

    with pytest.raises(OperationRunRefused, match="not valid at this time"):
        execute(
            prepared,
            operation_id="expired",
            subject="PROJECT/seat",
            grants_path=world.grants,
            journal=world.journal,
            timeout=30.0,
            clock=lambda: time.time() + 120,
        )
    assert world.invocations() == []
    assert run_state(world.journal, "expired") == ("absent", None)


def test_the_cli_runs_under_production_with_bundle_and_trust(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI runs under production with a bundle and trust; without trust it refuses."""
    _profile, trust = _production(world)
    command = [
        "ops",
        "run",
        "session.list",
        "--host",
        "workstation",
        "--vendor",
        "acme",
        "--component",
        "cli",
        "--component-version",
        "1.0.0",
        "--os",
        "linux",
        "--operation-id",
        "cli-prod",
        "--subject",
        "PROJECT/seat",
        "--grants",
        str(world.grants),
        "--journal",
        str(world.journal),
        "--catalogue",
        str(world.catalogue_file),
        "--profile",
        "production",
        "--bundle",
        str(world.grants.parent / "bundle.json"),
        "--trust",
        str(trust),
        "--require-environment",
        "python",
    ]

    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "confirmed_done"
    assert main([*command[:-4], "--operation-id", "cli-prod-2"]) == 2
    assert "signed by a key that is not trusted" in capsys.readouterr().err


def test_an_outcome_that_cannot_be_stored_is_unknown_as_inquire_says(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real vendor program makes the run directory read-only after its effect."""
    locked = run_directory(world.journal, "locked")
    monkeypatch.setenv("VENDOR_LOCK_DIR", str(locked))
    try:
        report = world.run("setting.write", {"key": "mode", "value": "lock"}, operation_id="locked")
    finally:
        locked.chmod(0o700)

    assert (report.state, report.observation) == ("unknown", "outcome_not_recorded")
    assert inquire(world.journal, "locked").observation == "start_without_outcome"
    assert world.invocations() == ["setting set mode lock"]


def test_a_failing_verifier_leaves_the_result_unknown_not_refused(world: World) -> None:
    """A verifier that fails leaves the run reported, its result unknown."""

    def broken(operation_id: str, state: str) -> Any:
        """Fail as a verifier whose storage is gone."""
        raise OSError("the verifier's disk is gone")

    report = world.run("session.list", verifier=broken)
    asked = inquire(world.journal, "session-list", verifier=broken)

    assert (report.state, report.verification) == ("confirmed_done", None)
    assert report.to_json()["verification_error"] == "the verifier failed: OSError"
    assert asked.verification_error == "the verifier failed: OSError"
    assert _status(report) == 3


def test_a_malformed_verification_request_is_refused_before_any_effect(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed digest is refused by run and inquire before anything is invoked."""
    run = [
        "ops",
        "run",
        "setting.write",
        "--host",
        "workstation",
        "--vendor",
        "acme",
        "--component",
        "cli",
        "--component-version",
        "1.0.0",
        "--os",
        "linux",
        "--arguments",
        json.dumps({"key": "mode", "value": "bad"}),
        "--operation-id",
        "bad-digest",
        "--subject",
        "PROJECT/seat",
        "--grants",
        str(world.grants),
        "--profile",
        "personal",
        "--journal",
        str(world.journal),
        "--catalogue",
        str(world.catalogue_file),
        "--verify-file",
        str(world.config),
        "--verify-sha256",
        "XYZ",
    ]

    assert main(run) == 2
    assert "64 lower-case hex" in capsys.readouterr().err
    assert world.invocations() == []
    assert run_state(world.journal, "bad-digest") == ("absent", None)
    inquiry = ["ops", "inquire", "--operation-id", "bad-digest", "--journal", str(world.journal)]
    assert main([*inquiry, "--verify-file", str(world.config), "--verify-sha256", "XYZ"]) == 2


def test_a_verbose_component_is_drained_into_a_bounded_buffer(world: World) -> None:
    """Two MiB on stdout and on stderr: one MiB is kept, every stdout byte is hashed."""
    report = world.run("diagnostics.read")
    outcome = read_record(run_directory(world.journal, "diagnostics-read"), "outcome.json")

    assert (report.state, report.output_truncated, report.output_complete) == (
        "confirmed_done",
        True,
        True,
    )
    assert report.output == "x" * MAX_OUTPUT_BYTES
    assert outcome is not None
    assert outcome["output_sha256"] == hashlib.sha256(b"x" * (2 << 20)).hexdigest()
    assert inquire(world.journal, "diagnostics-read").output_truncated is True


def test_output_held_open_by_a_left_behind_process_leaves_nothing_of_the_run_behind(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three runs whose vendor leaves a process holding stdout: no thread or descriptor stays."""
    pid_file = tmp_path / "orphan.pid"
    monkeypatch.setenv("VENDOR_ORPHAN_PID", str(pid_file))
    threads, descriptors = threading.active_count(), len(os.listdir("/dev/fd"))
    reports = []
    for number in range(3):
        started = time.monotonic()
        try:
            reports.append(world.run("turn.list", operation_id=f"orphan-{number}"))
        finally:
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
        assert time.monotonic() - started < 10

    assert [(r.state, r.output, r.output_complete) for r in reports] == [
        ("confirmed_done", "parent\n", False)
    ] * 3
    assert threading.active_count() == threads
    assert len(os.listdir("/dev/fd")) == descriptors


def test_every_new_journal_directory_is_synced_before_the_component_runs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real fsync calls are recorded by inode; those before the vendor's first effect count.

    ``deep`` exists already, as another runner would have left it before syncing
    its parent: seeing it is not enough, it is synced again.
    """
    (world.journal / "deep").mkdir(parents=True)
    journal = world.journal / "deep" / "journal"
    real_fsync = os.fsync
    before_effect: set[tuple[int, int]] = set()

    def recording(descriptor: int) -> None:
        """Record the synced inode while the vendor has not acted, then sync."""
        info = os.fstat(descriptor)
        if not world.log.exists():
            before_effect.add((info.st_dev, info.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    prepared = plan_personal(world.catalogue, world.target("session.list"), {})
    report = execute(
        prepared,
        operation_id="durable",
        subject="PROJECT/seat",
        grants_path=world.grants,
        journal=journal,
        timeout=30.0,
    )

    def inode(path: Path) -> tuple[int, int]:
        """Return the device and inode of ``path``."""
        info = path.stat()
        return info.st_dev, info.st_ino

    expected = {
        inode(path)
        for path in (
            world.journal.parent,
            world.journal,
            world.journal / "deep",
            journal,
            journal / "durable",
        )
    }
    assert report.state == "confirmed_done"
    assert expected <= before_effect
    assert inode(journal / "durable" / "start.json") in before_effect


def test_a_start_that_cannot_be_recorded_refuses_before_any_effect(world: World) -> None:
    """A journal that cannot take the start record refuses the run."""
    world.journal.mkdir()
    world.journal.chmod(0o500)
    try:
        with pytest.raises(OperationRunRefused, match="could not be recorded"):
            world.run("session.list", operation_id="unrecordable")
    finally:
        world.journal.chmod(0o700)
    assert world.invocations() == []


def test_the_report_names_its_local_authority(world: World) -> None:
    """The report and the inquiry name the local grants file as the authority."""
    report = world.run("session.list")

    assert report.authority == f"local operator grants file {world.grants}"
    assert inquire(world.journal, "session-list").authority == report.authority


def test_a_run_racing_another_runners_journal_creation_syncs_the_shared_path(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runner A creates the journal and is held at its first fsync; runner B runs meanwhile.

    B must not take A's unsynced directory for durable: it syncs the entry of
    the shared journal into its parent before its own component runs.
    """
    shared = world.journal / "shared"
    real_fsync = os.fsync
    held, release = threading.Event(), threading.Event()
    by_b_before_effect: set[tuple[int, int]] = set()

    def racing(descriptor: int) -> None:
        """Hold runner A at its first sync; record B's syncs before B's effect."""
        name = threading.current_thread().name
        if name == "runner-a" and not held.is_set():
            held.set()
            release.wait(30)
        elif name != "runner-a" and "session list --json" not in world.invocations():
            info = os.fstat(descriptor)
            by_b_before_effect.add((info.st_dev, info.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", racing)
    outcomes: list[str] = []
    runner_a = threading.Thread(
        target=lambda: outcomes.append(
            world.run("setting.read", operation_id="a", journal=shared).state
        ),
        name="runner-a",
    )
    runner_a.start()
    assert held.wait(30) and not (shared / "a" / "start.json").exists()
    report_b = world.run("session.list", operation_id="b", journal=shared)
    release.set()
    runner_a.join(30)

    def inode(path: Path) -> tuple[int, int]:
        """Return the device and inode of ``path``."""
        info = path.stat()
        return info.st_dev, info.st_ino

    assert (report_b.state, outcomes) == ("confirmed_done", ["confirmed_done"])
    assert {inode(world.journal), inode(shared), inode(shared / "b")} <= by_b_before_effect


class _NoSelector:
    """A selector the process may not create, as a kernel that refuses epoll would."""

    def __init__(self) -> None:
        """Refuse creation as a kernel without epoll permission would."""
        raise PermissionError(1, "Operation not permitted", "epoll_create1")


def test_a_collector_that_fails_after_the_start_never_abandons_the_run(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fault injection at the one call a sandbox can refuse: the component runs, is waited for."""
    monkeypatch.setattr("synapse_channel.operations.runner.selectors.DefaultSelector", _NoSelector)
    command = [
        "ops",
        "run",
        "setting.write",
        "--host",
        "workstation",
        "--vendor",
        "acme",
        "--component",
        "cli",
        "--component-version",
        "1.0.0",
        "--os",
        "linux",
        "--arguments",
        json.dumps({"key": "mode", "value": "sandboxed"}),
        "--operation-id",
        "sandboxed",
        "--subject",
        "PROJECT/seat",
        "--grants",
        str(world.grants),
        "--profile",
        "personal",
        "--journal",
        str(world.journal),
        "--catalogue",
        str(world.catalogue_file),
    ]

    assert main(command) == 3
    report = json.loads(capsys.readouterr().out)
    asked = inquire(world.journal, "sandboxed")

    assert (report["state"], report["returncode"]) == ("confirmed_done", 0)
    assert report["output_error"] == "the output could not be read: PermissionError"
    assert (asked.state, asked.output_error) == ("confirmed_done", report["output_error"])
    assert world.invocations() == ["setting set mode sandboxed"]
