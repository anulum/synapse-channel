# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — allow-listed setup executor real-surface tests

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.cli_arm import pid_alive
from synapse_channel.setup_authorization import build_setup_authorization
from synapse_channel.setup_contract import setup_schema
from synapse_channel.setup_executor import (
    SetupExecutionError,
    apply_setup,
    write_setup_receipt,
)
from synapse_channel.setup_ledger import SetupAuthorizationLedger, SetupLedgerError
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

NONCE = "executor_nonce_0123456789"

_MANAGER = r"""#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[0]).parent
state_path = root / "manager-state.json"
log_path = root / "manager-log.jsonl"
fail_path = root / "manager-fail"
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    state = {"units": {}}
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
command = args[1] if len(args) > 1 and args[0] == "--user" else ""
unit = args[-1] if "--" in args else ""
failure = fail_path.read_text(encoding="utf-8").strip() if fail_path.exists() else ""
arm_failures = {"arm-enable", "arm-enable-wrong-state", "arm-enable-bad-restore-pid"}
arm_enable_failure = failure in arm_failures and command == "enable" and "synapse-arm@" in unit
if failure == "wrong-restore-state" and command == "is-active":
    raise SystemExit(0)
if failure == "bad-state" and command == "is-active":
    raise SystemExit(9)
if failure == "bad-pid" and command == "show":
    print("not-a-pid")
    raise SystemExit(0)
if failure == "restart-once" and command == "restart":
    fail_path.unlink()
    raise SystemExit(1)
if arm_enable_failure:
    tamper_path = root / "tamper-path"
    if tamper_path.exists():
        Path(tamper_path.read_text(encoding="utf-8")).write_text("tampered\n", encoding="utf-8")
    if failure == "arm-enable-wrong-state":
        fail_path.write_text("wrong-restore-state", encoding="utf-8")
    if failure == "arm-enable-bad-restore-pid":
        fail_path.write_text("bad-pid", encoding="utf-8")
    raise SystemExit(1)
if failure and failure == command:
    raise SystemExit(1)

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

def stop(record):
    pid = int(record.get("pid", 0))
    if pid > 1:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    record["active"] = False
    record["pid"] = 0

def start(record):
    stop(record)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    record["active"] = True
    record["pid"] = process.pid

record = state["units"].setdefault(unit, {"active": False, "enabled": False, "pid": 0})
if failure.startswith("drift-on-state:") and command == "is-enabled":
    record["active"] = True
    record["pid"] = int(failure.split(":", 1)[1])
    save()
    fail_path.unlink()
if command in {"show-environment", "daemon-reload"}:
    raise SystemExit(0)
if command == "is-active":
    raise SystemExit(0 if record["active"] else 3)
if command == "is-enabled":
    raise SystemExit(0 if record["enabled"] else 1)
if command == "show":
    print(record["pid"])
    raise SystemExit(0)
if command == "enable":
    record["enabled"] = True
    if "--now" in args and failure != "no-start":
        start(record)
    save()
    if failure.startswith("kill-protected:"):
        os.kill(int(failure.split(":", 1)[1]), signal.SIGTERM)
    raise SystemExit(0)
if command == "disable":
    record["enabled"] = False
    if "--now" in args:
        stop(record)
    save()
    raise SystemExit(0)
if command == "restart":
    start(record)
    save()
    raise SystemExit(0)
if command == "stop":
    stop(record)
    save()
    raise SystemExit(0)
raise SystemExit(2)
"""


def _profile() -> SetupProfile:
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    return profile


def _inspection(
    systemctl: Path,
    overrides: dict[str, str],
    *,
    hub_pid: int | None = None,
    generation_override: dict[str, str] | None = None,
) -> dict[str, object]:
    profile = _profile()
    statuses = {item.requirement_id: "pass" for item in profile.requirements}
    statuses.update(overrides)
    values: dict[str, object] = {
        "package": {"name": "synapse-channel", "version": "0.99.24"},
        "python": {"executable": sys.executable, "version": "3.12.0"},
        "platform": {"system": "Linux", "release": "test", "machine": "x86_64"},
        "executable": "/usr/bin/true",
        "identity": {"project": "DEMO", "identity": "DEMO/codex-one"},
        "hub": {"uri": "ws://localhost:8876"},
        "waiter": {"identity": "DEMO/codex-one-rx"},
        "service_manager": {
            "kind": "systemd-user",
            "executable": str(systemctl),
            "hub_pid": hub_pid or 0,
        },
    }
    if generation_override:
        cast(dict[str, object], values["package"])["version"] = generation_override.get(
            "package_version", "0.99.24"
        )
        cast(dict[str, object], values["python"])["executable"] = generation_override.get(
            "python_executable", sys.executable
        )
        cast(dict[str, object], values["platform"])["system"] = generation_override.get(
            "platform_system", "Linux"
        )
        values["executable"] = generation_override.get("synapse_executable", "/usr/bin/true")
        cast(dict[str, object], values["service_manager"])["executable"] = generation_override.get(
            "service_manager_executable", str(systemctl)
        )
    checks = [
        {
            "id": item.requirement_id,
            "status": statuses[item.requirement_id],
            "required": item.required,
            "value": values[item.requirement_id],
            "detail": "Observed through the public setup inspection contract.",
            "remedy": "",
        }
        for item in profile.requirements
    ]
    return {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": all(
            statuses[item.requirement_id] == "pass"
            for item in profile.requirements
            if item.required
        ),
        "target": {
            "uri": "ws://localhost:8876",
            "project": "DEMO",
            "identity": "DEMO/codex-one",
        },
        "summary": {
            status_name: sum(check["status"] == status_name for check in checks)
            for status_name in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }


def _authorization(
    plan: dict[str, object],
    *,
    nonce: str = NONCE,
    restart_pid: int | None = None,
) -> dict[str, object]:
    return build_setup_authorization(
        plan,
        confirm_digest=cast(str, plan["plan_digest"]),
        nonce=nonce,
        expires_in=300,
        restart_pid=restart_pid,
        clock=lambda: 100.0,
    )


def _inspection_runner(document: dict[str, object]) -> Callable[..., object]:
    async def inspect(*_args: object, **kwargs: object) -> dict[str, object]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["SYN_PROJECT"] == "DEMO"
        assert environment["SYN_IDENTITY"] == "DEMO/codex-one"
        return document

    return inspect


def _run_apply(
    plan: dict[str, object],
    authorization: dict[str, object],
    *,
    inspection: dict[str, object],
    home: Path,
    ledger: Path,
    protected_pids: tuple[int, ...] = (),
    receipt: Path | None = None,
    clock: Callable[[], float] = lambda: 101.0,
) -> dict[str, object]:
    return asyncio.run(
        apply_setup(
            plan,
            authorization,
            confirm_digest=cast(str, plan["plan_digest"]),
            protected_pids=protected_pids,
            receipt_path=receipt,
            env={"HOME": str(home)},
            ledger_directory=ledger,
            inspection_runner=_inspection_runner(inspection),  # type: ignore[arg-type]
            clock=clock,
        )
    )


@contextmanager
def _process() -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(  # nosec B603 - fixed test process
        [sys.executable, "-c", "import time; time.sleep(300)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _manager(tmp_path: Path) -> Path:
    if platform.system() != "Linux":
        pytest.skip("Linux/systemd-user executor contract: docs/machine-readable-setup.md")
    executable = tmp_path / "systemctl"
    executable.write_text(_MANAGER, encoding="utf-8")
    executable.chmod(0o700)
    systemd_escape = shutil.which("systemd-escape")
    assert systemd_escape is not None
    (tmp_path / "systemd-escape").symlink_to(systemd_escape)
    return executable


def _unit_directory(home: Path) -> Path:
    home.mkdir(exist_ok=True)
    directory = home
    for component in (".config", "systemd", "user"):
        directory /= component
        directory.mkdir(exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return directory


def _manager_state(systemctl: Path) -> dict[str, object]:
    path = systemctl.parent / "manager-state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"units": {}}


def _manager_commands(systemctl: Path) -> list[list[str]]:
    path = systemctl.parent / "manager-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _stop_manager_processes(systemctl: Path) -> None:
    units = cast(dict[str, dict[str, object]], _manager_state(systemctl)["units"])
    for record in units.values():
        pid = int(cast(int, record.get("pid", 0)))
        if pid > 1 and pid_alive(pid):
            os.kill(pid, signal.SIGTERM)


@pytest.mark.skipif(platform.system() == "Linux", reason="Native non-Linux refusal contract")
def test_apply_on_unsupported_host_is_inert(tmp_path: Path) -> None:
    inspection = _inspection(Path("/usr/bin/true"), {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    with pytest.raises(SetupExecutionError, match="application_platform_unsupported"):
        asyncio.run(
            apply_setup(
                plan,
                authorization,
                confirm_digest=cast(str, plan["plan_digest"]),
                env={"HOME": str(tmp_path / "home")},
                ledger_directory=tmp_path / "ledger",
                receipt_path=tmp_path / "receipt.json",
                clock=lambda: 101.0,
            )
        )
    assert list(tmp_path.iterdir()) == []


def test_apply_executes_both_allowlisted_effects_and_replay_is_inert(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"hub": "fail", "waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    receipt_path = tmp_path / "receipt.json"
    try:
        with _process() as protected:
            receipt = _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
                protected_pids=(protected.pid,),
                receipt=receipt_path,
            )
            assert protected.poll() is None
        assert receipt["outcome"] == "applied"
        effects = cast(list[dict[str, object]], receipt["effects"])
        assert [item["id"] for item in effects] == [
            "establish_local_loopback_hub",
            "establish_identity_waiter",
        ]
        jsonschema.validate(receipt, setup_schema())
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**receipt, "ledger_state": "failed"}, setup_schema())
        assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
        assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
        unit_dir = home / ".config" / "systemd" / "user"
        assert " hub --port=8876 " in (unit_dir / "synapse-hub.service").read_text()
        assert " arm " in (unit_dir / "synapse-arm@.service").read_text()
        assert stat.S_IMODE((unit_dir / "synapse-hub.service").stat().st_mode) == 0o600
        commands_before_replay = _manager_commands(systemctl)
        with pytest.raises(SetupLedgerError) as caught:
            _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
            )
        assert caught.value.code == "authorization_replayed"
        assert _manager_commands(systemctl) == commands_before_replay
        with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
            record = ledger.get(cast(str, authorization["authorization_digest"]))
        assert record is not None
        assert record.state == "applied"
        assert record.effect_receipt_digest == receipt["receipt_digest"]
    finally:
        _stop_manager_processes(systemctl)


def test_apply_recovers_original_unit_and_service_state_after_effect_failure(
    tmp_path: Path,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("arm-enable", encoding="utf-8")
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    unit_dir = _unit_directory(home)
    old = unit_dir / "synapse-arm@.service"
    old.write_bytes(b"\xffold waiter unit\n")
    old.chmod(0o640)

    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )

    assert receipt["outcome"] == "recovered"
    assert receipt["recovery"] == "complete"
    jsonschema.validate(receipt, setup_schema())
    assert receipt["effects"] == [
        {
            "id": "establish_identity_waiter",
            "unit": "synapse-arm@DEMO-codex\\x2done.service",
            "outcome": "failed",
        }
    ]
    assert old.read_bytes() == b"\xffold waiter unit\n"
    assert stat.S_IMODE(old.stat().st_mode) == 0o640
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        record = ledger.get(cast(str, authorization["authorization_digest"]))
    assert record is not None
    assert record.state == "recovered"
    assert record.effect_receipt_digest == receipt["effect_receipt_digest"]
    assert record.recovery_receipt_digest == receipt["receipt_digest"]


def test_apply_restart_requires_the_exact_fresh_pid_and_preserves_other_processes(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    try:
        with _process() as hub, _process() as protected:
            state = {
                "units": {
                    "synapse-hub.service": {
                        "active": True,
                        "enabled": True,
                        "pid": hub.pid,
                    }
                }
            }
            (tmp_path / "manager-state.json").write_text(json.dumps(state), encoding="utf-8")
            inspection = _inspection(systemctl, {"hub": "fail"}, hub_pid=hub.pid)
            plan = build_setup_plan(_profile(), inspection)
            authorization = _authorization(plan, restart_pid=hub.pid)
            receipt = _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
                protected_pids=(protected.pid,),
            )
            assert receipt["outcome"] == "applied"
            assert protected.poll() is None
            hub.wait(timeout=5)
            new_pid = cast(dict[str, dict[str, int]], _manager_state(systemctl)["units"])[
                "synapse-hub.service"
            ]["pid"]
            assert new_pid != hub.pid
            assert pid_alive(new_pid)
            commands = _manager_commands(systemctl)
            assert ["--user", "restart", "--", "synapse-hub.service"] in commands
    finally:
        _stop_manager_processes(systemctl)


def test_apply_refuses_generation_drift_before_authorization_reservation(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    changed = _inspection(
        systemctl,
        {"waiter": "fail"},
        generation_override={"synapse_executable": "/usr/bin/false"},
    )
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=changed,
            home=home,
            ledger=tmp_path / "ledger",
        )
    assert caught.value.code == "application_target_changed"
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        assert ledger.get(cast(str, authorization["authorization_digest"])) is None
    assert not (home / ".config").exists()


def test_apply_refuses_missing_protected_pid_and_relative_home_before_effect(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=tmp_path / "ledger",
            protected_pids=(2_147_483_647,),
        )
    assert caught.value.code == "application_protected_process_missing"

    with pytest.raises(SetupExecutionError) as caught:
        asyncio.run(
            apply_setup(
                plan,
                authorization,
                confirm_digest=cast(str, plan["plan_digest"]),
                env={"HOME": "relative"},
                ledger_directory=tmp_path / "other-ledger",
                inspection_runner=_inspection_runner(inspection),  # type: ignore[arg-type]
                clock=lambda: 101.0,
            )
        )
    assert caught.value.code == "application_target_changed"


def test_receipt_writer_is_atomic_private_and_refuses_unsafe_targets(tmp_path: Path) -> None:
    receipt: dict[str, object] = {
        "document_kind": "application_receipt",
        "outcome": "applied",
    }
    target = tmp_path / "receipt.json"
    write_setup_receipt(target.resolve(), receipt)
    write_setup_receipt(target.resolve(), {**receipt, "outcome": "recovered"})
    assert json.loads(target.read_text(encoding="utf-8"))["outcome"] == "recovered"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    link = tmp_path / "link.json"
    link.symlink_to(target)
    for unsafe in (Path("relative.json"), tmp_path / "missing" / "receipt.json", link):
        with pytest.raises(SetupExecutionError) as caught:
            write_setup_receipt(unsafe, receipt)
        assert caught.value.code == "application_receipt_unavailable"

    # The target fits the filesystem limit; its atomic temporary prefix cannot.
    maximum_name = "r" * os.pathconf(tmp_path, "PC_NAME_MAX")
    with pytest.raises(SetupExecutionError) as caught:
        write_setup_receipt(tmp_path / maximum_name, receipt)
    assert caught.value.code == "application_receipt_unavailable"
    assert caught.value.receipt == receipt


@pytest.mark.parametrize("clock", [lambda: float("inf"), lambda: -1.0])
def test_apply_refuses_invalid_execution_time_before_opening_the_ledger(
    tmp_path: Path,
    clock: Callable[[], float],
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=tmp_path / "home",
            ledger=tmp_path / "ledger",
            clock=clock,
        )
    assert caught.value.code == "application_target_changed"
    assert not (tmp_path / "ledger").exists()


def test_apply_refuses_a_second_digest_confirmation_mismatch(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    with pytest.raises(SetupExecutionError) as caught:
        asyncio.run(
            apply_setup(
                plan,
                authorization,
                confirm_digest="0" * 64,
                env={"HOME": str(tmp_path / "home")},
                ledger_directory=tmp_path / "ledger",
                inspection_runner=_inspection_runner(inspection),  # type: ignore[arg-type]
                clock=lambda: 101.0,
            )
        )
    assert caught.value.code == "digest_mismatch"


def test_apply_consumes_authority_without_mutation_when_effect_is_already_satisfied(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    planned_inspection = _inspection(systemctl, {"waiter": "fail"})
    ready_inspection = _inspection(systemctl, {})
    plan = build_setup_plan(_profile(), planned_inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    receipt = _run_apply(
        plan,
        authorization,
        inspection=ready_inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "applied"
    assert receipt["effects"] == [
        {
            "id": "establish_identity_waiter",
            "unit": "",
            "outcome": "already_satisfied",
        }
    ]
    assert not (home / ".config").exists()
    assert not (tmp_path / "manager-log.jsonl").exists()


@pytest.mark.parametrize(
    "fresh_overrides",
    [
        {"hub": "fail", "waiter": "fail"},
        {"waiter": "unavailable"},
    ],
)
def test_apply_refuses_new_or_changed_fresh_effects(
    tmp_path: Path,
    fresh_overrides: dict[str, str],
) -> None:
    systemctl = _manager(tmp_path)
    planned = _inspection(systemctl, {"waiter": "fail"})
    fresh = _inspection(systemctl, fresh_overrides)
    plan = build_setup_plan(_profile(), planned)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=fresh,
            home=home,
            ledger=tmp_path / "ledger",
        )
    assert caught.value.code == "application_target_changed"


def test_apply_bounds_a_malformed_fresh_inspection(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection={},
            home=home,
            ledger=tmp_path / "ledger",
        )
    assert caught.value.code == "application_target_changed"


@pytest.mark.parametrize("protected", [(True,), (1,)])
def test_apply_rejects_invalid_protected_pid_values(
    tmp_path: Path,
    protected: tuple[int, ...],
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan)
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=tmp_path / "ledger",
            protected_pids=protected,
        )
    assert caught.value.code == "application_protected_process_missing"


def test_apply_refuses_to_treat_the_authorized_restart_target_as_protected(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    with _process() as hub:
        inspection = _inspection(systemctl, {"hub": "fail"}, hub_pid=hub.pid)
        plan = build_setup_plan(_profile(), inspection)
        authorization = _authorization(plan, restart_pid=hub.pid)
        with pytest.raises(SetupExecutionError) as caught:
            _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
                protected_pids=(hub.pid,),
            )
    assert caught.value.code == "application_protected_process_missing"


@pytest.mark.parametrize("failure", ["bad-state", "bad-pid", "no-start"])
def test_apply_recovers_when_the_service_boundary_returns_invalid_evidence(
    tmp_path: Path,
    failure: str,
) -> None:
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text(failure, encoding="utf-8")
    inspection = _inspection(systemctl, {"hub": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_{failure.replace('-', '_')}")
    home = tmp_path / "home"
    home.mkdir()
    try:
        receipt = _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=tmp_path / "ledger",
        )
        assert receipt["outcome"] == "recovered"
    finally:
        _stop_manager_processes(systemctl)


@pytest.mark.parametrize(
    "companion_output",
    [None, "invalid-unit", "synapse-hub.service", "synapse-arm@.service", "x" * 1025 + ".service"],
)
def test_apply_recovers_when_systemd_escape_is_missing_or_invalid(
    tmp_path: Path,
    companion_output: str | None,
) -> None:
    systemctl = _manager(tmp_path)
    companion = tmp_path / "systemd-escape"
    companion.unlink()
    if companion_output is not None:
        companion.write_text(
            f"#!/bin/sh\nprintf '%s' '{companion_output}'\n",
            encoding="utf-8",
        )
        companion.chmod(0o700)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    suffix = "missing" if companion_output is None else str(len(companion_output))
    authorization = _authorization(plan, nonce=f"{NONCE}_escape_{suffix}")
    home = tmp_path / "home"
    home.mkdir()
    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "recovered"
    effects = cast(list[dict[str, object]], receipt["effects"])
    assert effects[0]["outcome"] == "failed"


def test_apply_refuses_a_symlinked_service_directory_ancestor_without_traversal(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_symlinked_ancestor")
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".config").symlink_to(outside, target_is_directory=True)

    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )

    assert receipt["outcome"] == "recovered"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("unsafe_kind", ["symlink", "oversized"])
def test_apply_refuses_unsafe_existing_unit_leaves_and_recovers(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"hub": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_{unsafe_kind}")
    home = tmp_path / "home"
    unit_dir = _unit_directory(home)
    unit = unit_dir / "synapse-hub.service"
    if unsafe_kind == "symlink":
        target = tmp_path / "outside"
        target.write_text("outside\n", encoding="utf-8")
        unit.symlink_to(target)
    else:
        unit.write_bytes(b"x" * 1_048_577)
    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "recovered"
    effects = cast(list[dict[str, object]], receipt["effects"])
    assert effects[0]["outcome"] == "failed"


def test_apply_recovers_a_new_unit_by_removing_only_that_exact_leaf(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("arm-enable", encoding="utf-8")
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_new_unit")
    home = tmp_path / "home"
    home.mkdir()
    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "recovered"
    assert not (home / ".config" / "systemd" / "user" / "synapse-arm@.service").exists()


def test_apply_restores_enabled_active_state_after_one_restart_failure(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("restart-once", encoding="utf-8")
    home = tmp_path / "home"
    unit_dir = _unit_directory(home)
    unit = unit_dir / "synapse-hub.service"
    unit.write_text("old hub unit\n", encoding="utf-8")
    with _process() as hub:
        state = {
            "units": {"synapse-hub.service": {"active": True, "enabled": True, "pid": hub.pid}}
        }
        (tmp_path / "manager-state.json").write_text(json.dumps(state), encoding="utf-8")
        inspection = _inspection(systemctl, {"hub": "fail"}, hub_pid=hub.pid)
        plan = build_setup_plan(_profile(), inspection)
        authorization = _authorization(plan, nonce=f"{NONCE}_restart_once", restart_pid=hub.pid)
        receipt = _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=tmp_path / "ledger",
        )
        assert receipt["outcome"] == "recovered"
        assert unit.read_text(encoding="utf-8") == "old hub unit\n"
    _stop_manager_processes(systemctl)


def test_apply_records_recovery_failure_when_installed_unit_is_replaced(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("arm-enable", encoding="utf-8")
    home = tmp_path / "home"
    unit_dir = _unit_directory(home)
    unit = unit_dir / "synapse-arm@.service"
    unit.write_text("old waiter unit\n", encoding="utf-8")
    (tmp_path / "tamper-path").write_text(str(unit), encoding="utf-8")
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_tamper")
    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "recovery_failed"
    assert receipt["recovery"] == "failed"
    jsonschema.validate(receipt, setup_schema())
    assert unit.read_text(encoding="utf-8") == "tampered\n"
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        record = ledger.get(cast(str, authorization["authorization_digest"]))
    assert record is not None
    assert record.state == "failed"


def test_apply_refuses_an_unsafe_or_contended_host_lock(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    home = tmp_path / "home"
    home.mkdir()
    ledger_dir = tmp_path / "ledger"
    with SetupAuthorizationLedger(ledger_dir):
        pass
    target = tmp_path / "lock-target"
    target.write_text("not a lock\n", encoding="utf-8")
    (ledger_dir / "setup-apply.lock").symlink_to(target)
    authorization = _authorization(plan, nonce=f"{NONCE}_unsafe_lock")
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=ledger_dir,
        )
    assert caught.value.code == "application_lock_unavailable"

    (ledger_dir / "setup-apply.lock").unlink()

    async def contend() -> tuple[dict[str, object], SetupExecutionError]:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_inspection(*_args: object, **_kwargs: object) -> dict[str, object]:
            entered.set()
            await release.wait()
            return inspection

        first_authorization = _authorization(plan, nonce=f"{NONCE}_lock_first")
        second_authorization = _authorization(plan, nonce=f"{NONCE}_lock_second")
        first = asyncio.create_task(
            apply_setup(
                plan,
                first_authorization,
                confirm_digest=cast(str, plan["plan_digest"]),
                env={"HOME": str(home)},
                ledger_directory=ledger_dir,
                inspection_runner=slow_inspection,
                clock=lambda: 101.0,
            )
        )
        await entered.wait()
        with pytest.raises(SetupExecutionError) as blocked:
            await apply_setup(
                plan,
                second_authorization,
                confirm_digest=cast(str, plan["plan_digest"]),
                env={"HOME": str(home)},
                ledger_directory=ledger_dir,
                inspection_runner=_inspection_runner(inspection),  # type: ignore[arg-type]
                clock=lambda: 101.0,
            )
        release.set()
        return await first, blocked.value

    try:
        first_receipt, contention = asyncio.run(contend())
        assert first_receipt["outcome"] == "applied"
        assert contention.code == "application_lock_unavailable"
    finally:
        _stop_manager_processes(systemctl)


def test_apply_recovers_without_effect_when_a_required_directory_is_group_writable(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_unsafe_directory")
    home = tmp_path / "home"
    home.mkdir()
    unsafe = home / "synapse"
    unsafe.mkdir(mode=0o770)
    unsafe.chmod(0o770)
    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )
    assert receipt["outcome"] == "recovered"
    assert receipt["effects"] == []
    assert unsafe.exists()


def test_apply_recovers_without_effect_when_home_directory_is_missing(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_missing_home")
    home = tmp_path / "missing-home"

    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )

    assert receipt["outcome"] == "recovered"
    assert receipt["effects"] == []
    assert not home.exists()


def test_apply_refuses_a_nonregular_host_lock_leaf(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_fifo_lock")
    home = tmp_path / "home"
    home.mkdir()
    ledger_dir = tmp_path / "ledger"
    with SetupAuthorizationLedger(ledger_dir):
        pass
    os.mkfifo(ledger_dir / "setup-apply.lock", 0o600)
    with pytest.raises(SetupExecutionError) as caught:
        _run_apply(
            plan,
            authorization,
            inspection=inspection,
            home=home,
            ledger=ledger_dir,
        )
    assert caught.value.code == "application_lock_unavailable"


def test_apply_refuses_restart_pid_drift_before_reserving_or_writing(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    try:
        with _process() as authorized_hub, _process() as replacement_hub:
            state = {
                "units": {
                    "synapse-hub.service": {
                        "active": True,
                        "enabled": True,
                        "pid": replacement_hub.pid,
                    }
                }
            }
            (tmp_path / "manager-state.json").write_text(json.dumps(state), encoding="utf-8")
            inspection = _inspection(
                systemctl,
                {"hub": "fail"},
                hub_pid=authorized_hub.pid,
            )
            plan = build_setup_plan(_profile(), inspection)
            authorization = _authorization(
                plan,
                nonce=f"{NONCE}_pid_drift",
                restart_pid=authorized_hub.pid,
            )
            with pytest.raises(SetupExecutionError) as caught:
                _run_apply(
                    plan,
                    authorization,
                    inspection=inspection,
                    home=home,
                    ledger=tmp_path / "ledger",
                )
            assert caught.value.code == "application_target_changed"
            assert authorized_hub.poll() is None
            with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
                assert ledger.get(cast(str, authorization["authorization_digest"])) is None
    finally:
        _stop_manager_processes(systemctl)


def test_apply_recovers_restart_pid_drift_between_reservation_and_restart(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    try:
        with _process() as authorized_hub, _process() as replacement_hub:
            state = {
                "units": {
                    "synapse-hub.service": {
                        "active": True,
                        "enabled": True,
                        "pid": authorized_hub.pid,
                    }
                }
            }
            (tmp_path / "manager-state.json").write_text(json.dumps(state), encoding="utf-8")
            (tmp_path / "manager-fail").write_text(
                f"drift-on-state:{replacement_hub.pid}", encoding="utf-8"
            )
            inspection = _inspection(
                systemctl,
                {"hub": "fail"},
                hub_pid=authorized_hub.pid,
            )
            plan = build_setup_plan(_profile(), inspection)
            authorization = _authorization(
                plan,
                nonce=f"{NONCE}_late_pid_drift",
                restart_pid=authorized_hub.pid,
            )
            receipt = _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
            )
            assert receipt["outcome"] == "recovered"
            assert authorized_hub.poll() is None
    finally:
        _stop_manager_processes(systemctl)


def test_apply_records_failed_recovery_when_a_real_protected_process_dies(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    inspection = _inspection(systemctl, {"hub": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_protected_death")
    home = tmp_path / "home"
    home.mkdir()
    try:
        with _process() as protected:
            (tmp_path / "manager-fail").write_text(
                f"kill-protected:{protected.pid}", encoding="utf-8"
            )
            receipt = _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
                protected_pids=(protected.pid,),
            )
            protected.wait(timeout=5)
        assert receipt["outcome"] == "recovery_failed"
        protected_rows = receipt["protected_processes"]
        assert isinstance(protected_rows, list)
        assert any(
            row["pid"] == protected.pid and row["after_alive"] is False for row in protected_rows
        )
    finally:
        _stop_manager_processes(systemctl)


def test_apply_uses_start_time_when_completion_clock_becomes_invalid(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    planned = _inspection(systemctl, {"waiter": "fail"})
    ready = _inspection(systemctl, {})
    plan = build_setup_plan(_profile(), planned)
    authorization = _authorization(plan, nonce=f"{NONCE}_completion_clock")
    readings = iter((101.0, float("inf")))
    home = tmp_path / "home"
    home.mkdir()

    receipt = _run_apply(
        plan,
        authorization,
        inspection=ready,
        home=home,
        ledger=tmp_path / "ledger",
        clock=lambda: next(readings),
    )

    assert receipt["outcome"] == "applied"
    assert receipt["started_at"] == 101
    assert receipt["completed_at"] == 101


def test_apply_records_recovery_failure_when_restored_state_is_wrong(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("arm-enable-wrong-state", encoding="utf-8")
    inspection = _inspection(systemctl, {"waiter": "fail"})
    plan = build_setup_plan(_profile(), inspection)
    authorization = _authorization(plan, nonce=f"{NONCE}_wrong_restore_state")
    home = tmp_path / "home"
    home.mkdir()

    receipt = _run_apply(
        plan,
        authorization,
        inspection=inspection,
        home=home,
        ledger=tmp_path / "ledger",
    )

    assert receipt["outcome"] == "recovery_failed"
    assert receipt["recovery"] == "failed"


def test_apply_records_recovery_failure_when_restored_pid_is_invalid(tmp_path: Path) -> None:
    systemctl = _manager(tmp_path)
    (tmp_path / "manager-fail").write_text("arm-enable-bad-restore-pid", encoding="utf-8")
    home = tmp_path / "home"
    unit_dir = _unit_directory(home)
    (unit_dir / "synapse-arm@.service").write_text("old waiter unit\n", encoding="utf-8")
    waiter_unit = r"synapse-arm@DEMO-codex\x2done.service"
    try:
        with _process() as waiter:
            state = {"units": {waiter_unit: {"active": True, "enabled": True, "pid": waiter.pid}}}
            (tmp_path / "manager-state.json").write_text(json.dumps(state), encoding="utf-8")
            inspection = _inspection(systemctl, {"waiter": "fail"})
            plan = build_setup_plan(_profile(), inspection)
            authorization = _authorization(plan, nonce=f"{NONCE}_bad_restore_pid")

            receipt = _run_apply(
                plan,
                authorization,
                inspection=inspection,
                home=home,
                ledger=tmp_path / "ledger",
            )

            assert receipt["outcome"] == "recovery_failed"
            assert receipt["recovery"] == "failed"
    finally:
        _stop_manager_processes(systemctl)


def test_apply_recovers_when_protected_pid_disappears_after_inert_reservation(
    tmp_path: Path,
) -> None:
    systemctl = _manager(tmp_path)
    planned = _inspection(systemctl, {"waiter": "fail"})
    ready = _inspection(systemctl, {})
    plan = build_setup_plan(_profile(), planned)
    authorization = _authorization(plan, nonce=f"{NONCE}_inert_protected_race")
    home = tmp_path / "home"
    home.mkdir()
    calls = 0

    def disappears_after_reservation(_pid: int) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    receipt = asyncio.run(
        apply_setup(
            plan,
            authorization,
            confirm_digest=cast(str, plan["plan_digest"]),
            env={"HOME": str(home)},
            ledger_directory=tmp_path / "ledger",
            inspection_runner=_inspection_runner(ready),  # type: ignore[arg-type]
            probe=disappears_after_reservation,
            clock=lambda: 101.0,
        )
    )

    assert receipt["outcome"] == "recovery_failed"
    assert receipt["protected_processes"] == [
        {"pid": os.getppid(), "before_alive": True, "after_alive": False}
    ]
