# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — allow-listed machine setup executor and recovery
"""Apply one exact setup authorization with bounded, fail-closed recovery."""

from __future__ import annotations

import contextlib
import os
import platform
import stat

# All subprocess calls below use fixed argv and a ten-second timeout.
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

from synapse_channel.service_setup import render_arm_unit, render_hub_unit
from synapse_channel.setup_authorization import (
    MAX_RESTART_PID,
    SetupAuthorizationError,
    validate_setup_authorization,
    validate_setup_plan,
)
from synapse_channel.setup_contract import (
    SETUP_SCHEMA_VERSION,
    SetupErrorCode,
    canonical_json,
    document_digest,
)
from synapse_channel.setup_inspector import inspect_setup
from synapse_channel.setup_ledger import (
    SetupAuthorizationLedger,
    SetupLedgerError,
    default_setup_ledger_dir,
)
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile
from synapse_channel.shell_integration import pid_is_live_process

_MAX_UNIT_BYTES = 1_048_576
_COMMAND_TIMEOUT_SECONDS = 10.0
_LOCK_NAME = "setup-apply.lock"


class SetupCommandRunner(Protocol):
    """Callable compatible with the bounded subprocess calls used here."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run one fixed-argv command."""


SetupInspectionRunner = Callable[..., Awaitable[dict[str, object]]]
PidProbe = Callable[[int], bool]
Clock = Callable[[], float]


class SetupExecutionError(SetupAuthorizationError):
    """Stable executor refusal, optionally carrying a completed receipt."""

    def __init__(
        self,
        code: SetupErrorCode,
        *,
        receipt: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.receipt = receipt


class _EffectFailure(RuntimeError):
    """Internal value-free marker for a failed allow-listed effect."""


@dataclass(frozen=True, slots=True)
class _UnitSnapshot:
    effect_id: str
    unit: str
    path: Path
    content: bytes | None
    mode: int
    inode: tuple[int, int] | None
    active: bool
    enabled: bool
    installed_digest: str | None = None


def _integer_time(clock: Clock) -> int:
    try:
        value = int(clock())
    except (OverflowError, TypeError, ValueError) as exc:
        raise SetupExecutionError("application_target_changed") from exc
    if value < 0:
        raise SetupExecutionError("application_target_changed")
    return value


def _completion_time(clock: Clock, *, started_at: int) -> int:
    """Return a nondecreasing completion time without compromising recovery."""
    try:
        return max(started_at, _integer_time(clock))
    except SetupExecutionError:
        return started_at


def _run(
    runner: SetupCommandRunner,
    argv: list[str],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _EffectFailure from exc


def _run_ok(runner: SetupCommandRunner, argv: list[str]) -> None:
    if _run(runner, argv).returncode != 0:
        raise _EffectFailure


def _systemctl_state(
    systemctl: str,
    unit: str,
    *,
    runner: SetupCommandRunner,
) -> tuple[bool, bool]:
    active = _run(runner, [systemctl, "--user", "is-active", "--quiet", "--", unit])
    enabled = _run(runner, [systemctl, "--user", "is-enabled", "--quiet", "--", unit])
    if active.returncode not in {0, 1, 3, 4} or enabled.returncode not in {0, 1, 3, 4}:
        raise _EffectFailure
    return active.returncode == 0, enabled.returncode == 0


def _systemctl_pid(
    systemctl: str,
    unit: str,
    *,
    runner: SetupCommandRunner,
) -> int | None:
    completed = _run(
        runner,
        [
            systemctl,
            "--user",
            "show",
            "--property=MainPID",
            "--value",
            "--",
            unit,
        ],
    )
    try:
        pid = int(completed.stdout.strip())
    except ValueError:
        return None
    return pid if completed.returncode == 0 and 1 < pid <= MAX_RESTART_PID else None


def _escaped_waiter_unit(
    identity: str,
    *,
    systemctl: str,
    runner: SetupCommandRunner,
) -> str:
    executable = str(Path(systemctl).with_name("systemd-escape"))
    completed = _run(
        runner,
        [executable, "--template=synapse-arm@.service", "--", identity],
    )
    unit = completed.stdout.strip()
    if (
        completed.returncode != 0
        or not unit.startswith("synapse-arm@")
        or not unit.endswith(".service")
        or unit == "synapse-arm@.service"
        or len(unit) > 1024
        or any(character.isspace() or ord(character) < 32 for character in unit)
    ):
        raise _EffectFailure
    return unit


def _require_private_directory(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()  # pragma: no branch - foreign UID needs privilege
        or info.st_mode & 0o022
    ):
        raise _EffectFailure


def _ensure_directory(root: Path, parts: tuple[str, ...], *, created: list[Path]) -> None:
    """Open and create an owner-controlled path without following symlink leaves."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(root, flags)
        root_info = os.fstat(descriptor)
        if root_info.st_uid != os.geteuid():  # pragma: no cover - foreign UID needs privilege
            raise _EffectFailure
        cursor = root
        for component in parts:
            was_created = False
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                was_created = True
            except FileExistsError:
                pass
            cursor /= component
            if was_created:
                created.append(cursor)
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _require_private_directory(os.fstat(descriptor))
    except OSError as exc:
        raise _EffectFailure from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_snapshot(
    *,
    effect_id: str,
    unit: str,
    path: Path,
    active: bool,
    enabled: bool,
) -> _UnitSnapshot:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return _UnitSnapshot(effect_id, unit, path, None, 0o600, None, active, enabled)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > _MAX_UNIT_BYTES
        or before.st_uid != os.geteuid()  # pragma: no branch - foreign UID needs privilege
    ):
        raise _EffectFailure
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (  # pragma: no branch - inode/type changes require an OS-level race
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > _MAX_UNIT_BYTES
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise _EffectFailure  # pragma: no cover - same OS-level race
            content = os.read(descriptor, _MAX_UNIT_BYTES + 1)
            if len(content) > _MAX_UNIT_BYTES:  # pragma: no cover - concurrent growth race
                raise _EffectFailure
        finally:
            os.close(descriptor)
    except OSError as exc:  # pragma: no cover - leaf replacement race after lstat
        raise _EffectFailure from exc
    return _UnitSnapshot(
        effect_id,
        unit,
        path,
        content,
        stat.S_IMODE(before.st_mode),
        (before.st_dev, before.st_ino),
        active,
        enabled,
    )


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    expected: tuple[int, int] | None,
) -> str:
    current: tuple[int, int] | None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):  # pragma: no cover - leaf replacement race
            raise _EffectFailure
        current = (info.st_dev, info.st_ino)
    except FileNotFoundError:
        current = None
    if current != expected:  # pragma: no cover - inode replacement race after snapshot
        raise _EffectFailure
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return sha256(content).hexdigest()


def _install_unit(snapshot: _UnitSnapshot, content: str) -> _UnitSnapshot:
    payload = content.encode("utf-8")
    digest = _atomic_write(snapshot.path, payload, mode=0o600, expected=snapshot.inode)
    return _UnitSnapshot(
        snapshot.effect_id,
        snapshot.unit,
        snapshot.path,
        snapshot.content,
        snapshot.mode,
        snapshot.inode,
        snapshot.active,
        snapshot.enabled,
        digest,
    )


def _restore_unit(snapshot: _UnitSnapshot) -> None:
    installed_digest = cast(str, snapshot.installed_digest)
    current = _read_snapshot(
        effect_id=snapshot.effect_id,
        unit=snapshot.unit,
        path=snapshot.path,
        active=snapshot.active,
        enabled=snapshot.enabled,
    )
    if current.content is None or sha256(current.content).hexdigest() != installed_digest:
        raise _EffectFailure
    if snapshot.content is None:
        snapshot.path.unlink()
        return
    _atomic_write(
        snapshot.path,
        snapshot.content,
        mode=snapshot.mode,
        expected=current.inode,
    )


def _restore_service_state(
    snapshots: Sequence[_UnitSnapshot],
    *,
    systemctl: str,
    runner: SetupCommandRunner,
) -> None:
    if not snapshots:
        return
    for snapshot in reversed(snapshots):
        _restore_unit(snapshot)
    _run_ok(runner, [systemctl, "--user", "daemon-reload"])
    for snapshot in snapshots:
        if snapshot.enabled:
            _run_ok(runner, [systemctl, "--user", "enable", "--", snapshot.unit])
        else:
            _run_ok(runner, [systemctl, "--user", "disable", "--", snapshot.unit])
        action = "restart" if snapshot.active else "stop"
        _run_ok(runner, [systemctl, "--user", action, "--", snapshot.unit])
        active, enabled = _systemctl_state(systemctl, snapshot.unit, runner=runner)
        if (active, enabled) != (snapshot.active, snapshot.enabled):
            raise _EffectFailure
        if active:
            restored_pid = _systemctl_pid(systemctl, snapshot.unit, runner=runner)
            if restored_pid is None or not pid_is_live_process(restored_pid):
                raise _EffectFailure


@contextlib.contextmanager
def _host_lock(directory: Path) -> Iterator[None]:
    lock_path = directory / _LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise SetupExecutionError("application_lock_unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise SetupExecutionError("application_lock_unavailable")
        os.fchmod(descriptor, 0o600)
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SetupExecutionError("application_lock_unavailable") from exc
    except SetupExecutionError:
        os.close(descriptor)
        raise
    except (ImportError, OSError) as exc:  # pragma: no cover - post-open platform/FS failure
        os.close(descriptor)
        raise SetupExecutionError("application_lock_unavailable") from exc
    try:
        yield
    finally:
        os.close(descriptor)


def _protected_pids(
    requested: Sequence[int],
    *,
    restart_pid: int | None,
    probe: PidProbe,
) -> tuple[int, ...]:
    values = tuple(dict.fromkeys((os.getppid(), *requested)))
    if any(
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or not 1 < pid <= MAX_RESTART_PID
        or pid == restart_pid
        or not probe(pid)
        for pid in values
    ):
        raise SetupExecutionError("application_protected_process_missing")
    return values


def _fresh_effects(
    plan: dict[str, object], fresh: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if fresh.get("target") != plan["target"] or fresh.get("generation") != plan["generation"]:
        raise SetupExecutionError("application_target_changed")
    original = {
        cast(str, effect["id"]): effect for effect in cast(list[dict[str, object]], plan["effects"])
    }
    current = {
        cast(str, effect["id"]): effect
        for effect in cast(list[dict[str, object]], fresh["effects"])
    }
    if set(current) - set(original):
        raise SetupExecutionError("application_target_changed")
    pending: list[dict[str, object]] = []
    unchanged: list[dict[str, object]] = []
    for effect_id, authorized in original.items():
        observed = current.get(effect_id)
        if observed is None:
            unchanged.append(authorized)
        elif observed != authorized or observed.get("disposition") != "planned":
            raise SetupExecutionError("application_target_changed")
        else:
            pending.append(authorized)
    return pending, unchanged


def _receipt(
    *,
    plan: dict[str, object],
    authorization: dict[str, object],
    started_at: int,
    completed_at: int,
    outcome: str,
    ledger_state: str,
    effects: list[dict[str, object]],
    protected: tuple[int, ...],
    protected_after: bool,
    recovery: str,
    effect_receipt_digest: str | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "application_receipt",
        "profile": plan["profile"],
        "profile_version": plan["profile_version"],
        "plan_digest": plan["plan_digest"],
        "authorization_digest": authorization["authorization_digest"],
        "target": plan["target"],
        "started_at": started_at,
        "completed_at": completed_at,
        "outcome": outcome,
        "ledger_state": ledger_state,
        "effects": effects,
        "protected_processes": [
            {"pid": pid, "before_alive": True, "after_alive": protected_after} for pid in protected
        ],
        "recovery": recovery,
        "effect_receipt_digest": effect_receipt_digest,
    }
    document["receipt_digest"] = document_digest(document)
    return document


def _validate_receipt_path(path: Path) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise SetupExecutionError("application_receipt_unavailable")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise SetupExecutionError("application_receipt_unavailable")


def write_setup_receipt(path: str | Path, receipt: dict[str, object]) -> None:
    """Atomically write one owner-only application receipt."""
    target = Path(path)
    _validate_receipt_path(target)
    payload = (canonical_json(receipt) + "\n").encode("utf-8")
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
    except OSError as exc:
        raise SetupExecutionError("application_receipt_unavailable", receipt=receipt) from exc


async def apply_setup(
    plan: dict[str, object],
    authorization: dict[str, object],
    *,
    confirm_digest: str,
    protected_pids: Sequence[int] = (),
    receipt_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    ledger_directory: str | Path | None = None,
    runner: SetupCommandRunner = subprocess.run,
    inspection_runner: SetupInspectionRunner = inspect_setup,
    probe: PidProbe = pid_is_live_process,
    clock: Clock = time.time,
) -> dict[str, object]:
    """Consume and apply one exact local-single-user authorization.

    Validation, a fresh inspection, the host lock, protected-PID checks, and
    ledger reservation all complete before the first service-file mutation.
    Unknown, newly required, or changed effects are refused.
    """
    validated_plan = validate_setup_plan(plan)
    if confirm_digest != validated_plan["plan_digest"]:
        raise SetupExecutionError("digest_mismatch")
    started_at = _integer_time(clock)
    validated_authorization = validate_setup_authorization(
        validated_plan,
        authorization,
        now=started_at,
    )
    generation = cast(dict[str, str], validated_plan["generation"])
    if platform.system() != "Linux":  # pragma: no cover - platform-specific adapter refusal
        raise SetupExecutionError("application_platform_unsupported")
    if receipt_path is not None:
        _validate_receipt_path(Path(receipt_path))

    profile = cast(SetupProfile, get_setup_profile(cast(str, validated_plan["profile"])))
    target = cast(dict[str, str], validated_plan["target"])
    environment = os.environ if env is None else env
    home = Path(environment.get("HOME", str(Path.home())))
    if not home.is_absolute():
        raise SetupExecutionError("application_target_changed")
    ledger_dir = (
        default_setup_ledger_dir(env=environment)
        if ledger_directory is None
        else Path(ledger_directory)
    )
    restart = cast(dict[str, int] | None, validated_authorization["restart_authority"])
    restart_pid = None if restart is None else restart["pid"]

    with SetupAuthorizationLedger(ledger_dir) as ledger, _host_lock(ledger.directory):
        inspection_env = {
            **environment,
            "SYN_PROJECT": target["project"],
            "SYN_IDENTITY": target["identity"],
        }
        fresh_inspection = await inspection_runner(
            profile,
            uri=target["uri"],
            project=None,
            agent_id=None,
            env=inspection_env,
        )
        try:
            fresh_plan = build_setup_plan(profile, fresh_inspection)
            pending, unchanged = _fresh_effects(validated_plan, fresh_plan)
        except SetupExecutionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise SetupExecutionError("application_target_changed") from exc
        protected = _protected_pids(protected_pids, restart_pid=restart_pid, probe=probe)
        systemctl = generation["service_manager_executable"]
        if restart_pid is not None and (
            _systemctl_pid(systemctl, "synapse-hub.service", runner=runner) != restart_pid
            or not probe(restart_pid)
        ):
            raise SetupExecutionError("application_target_changed")
        ledger.reserve(validated_plan, validated_authorization, now=started_at)

        unit_dir = home / ".config" / "systemd" / "user"
        created: list[Path] = []
        snapshots: list[_UnitSnapshot] = []
        effect_receipts = [
            {"id": effect["id"], "unit": "", "outcome": "already_satisfied"} for effect in unchanged
        ]
        current_effect_id: str | None = None
        current_unit = ""
        try:
            if pending:
                for parts in (
                    (".config", "systemd", "user"),
                    ("synapse",),
                    (".local", "share", "synapse"),
                ):
                    _ensure_directory(home, parts, created=created)
            for effect in pending:
                effect_id = cast(str, effect["id"])
                current_effect_id = effect_id
                current_unit = ""
                if effect_id == "establish_local_loopback_hub":
                    unit = "synapse-hub.service"
                    body = render_hub_unit(synapse_bin=generation["synapse_executable"])
                else:
                    unit = _escaped_waiter_unit(
                        target["identity"], systemctl=systemctl, runner=runner
                    )
                    body = render_arm_unit(
                        synapse_bin=generation["synapse_executable"],
                        uri=target["uri"],
                    )
                current_unit = unit
                active, enabled = _systemctl_state(systemctl, unit, runner=runner)
                template = (
                    "synapse-arm@.service" if effect_id == "establish_identity_waiter" else unit
                )
                snapshot = _read_snapshot(
                    effect_id=effect_id,
                    unit=unit,
                    path=unit_dir / template,
                    active=active,
                    enabled=enabled,
                )
                snapshots.append(_install_unit(snapshot, body))
                current_effect_id = None
                current_unit = ""

            if snapshots:
                _run_ok(runner, [systemctl, "--user", "daemon-reload"])
            for snapshot in snapshots:
                current_effect_id = snapshot.effect_id
                current_unit = snapshot.unit
                if snapshot.effect_id == "establish_local_loopback_hub" and restart_pid is not None:
                    if _systemctl_pid(systemctl, snapshot.unit, runner=runner) != restart_pid:
                        raise SetupExecutionError("application_target_changed")
                    _run_ok(runner, [systemctl, "--user", "enable", "--", snapshot.unit])
                    command = [systemctl, "--user", "restart", "--", snapshot.unit]
                else:
                    command = [systemctl, "--user", "enable", "--now", "--", snapshot.unit]
                _run_ok(runner, command)
                active, enabled = _systemctl_state(systemctl, snapshot.unit, runner=runner)
                service_pid = _systemctl_pid(systemctl, snapshot.unit, runner=runner)
                if not active or not enabled or service_pid is None or not probe(service_pid):
                    raise _EffectFailure
                effect_receipts.append(
                    {"id": snapshot.effect_id, "unit": snapshot.unit, "outcome": "applied"}
                )
                current_effect_id = None
                current_unit = ""
                if not all(probe(pid) for pid in protected):
                    raise SetupExecutionError("application_protected_process_missing")
            protected_after = all(probe(pid) for pid in protected)
            if not protected_after:
                raise SetupExecutionError("application_protected_process_missing")
            completed_at = _completion_time(clock, started_at=started_at)
            receipt = _receipt(
                plan=validated_plan,
                authorization=validated_authorization,
                started_at=started_at,
                completed_at=completed_at,
                outcome="applied",
                ledger_state="applied",
                effects=effect_receipts,
                protected=protected,
                protected_after=True,
                recovery="not_required",
            )
            ledger.finish(
                cast(str, validated_authorization["authorization_digest"]),
                outcome="applied",
                receipt_digest=cast(str, receipt["receipt_digest"]),
            )
        except (
            SetupAuthorizationError,
            SetupLedgerError,
            _EffectFailure,
            OSError,
            ValueError,
        ):
            if current_effect_id is not None:
                effect_receipts.append(
                    {"id": current_effect_id, "unit": current_unit, "outcome": "failed"}
                )
            failure_time = _completion_time(clock, started_at=started_at)
            failed = _receipt(
                plan=validated_plan,
                authorization=validated_authorization,
                started_at=started_at,
                completed_at=failure_time,
                outcome="failed",
                ledger_state="failed",
                effects=effect_receipts,
                protected=protected,
                protected_after=all(probe(pid) for pid in protected),
                recovery="pending",
            )
            failure_digest = cast(str, failed["receipt_digest"])
            try:
                ledger.finish(
                    cast(str, validated_authorization["authorization_digest"]),
                    outcome="failed",
                    receipt_digest=failure_digest,
                )
                _restore_service_state(snapshots, systemctl=systemctl, runner=runner)
                for directory in reversed(created):
                    with contextlib.suppress(OSError):
                        directory.rmdir()
                protected_after = all(probe(pid) for pid in protected)
                if not protected_after:
                    raise _EffectFailure
                recovered = _receipt(
                    plan=validated_plan,
                    authorization=validated_authorization,
                    started_at=started_at,
                    completed_at=_completion_time(clock, started_at=started_at),
                    outcome="recovered",
                    ledger_state="recovered",
                    effects=effect_receipts,
                    protected=protected,
                    protected_after=True,
                    recovery="complete",
                    effect_receipt_digest=failure_digest,
                )
                ledger.recover(
                    cast(str, validated_authorization["authorization_digest"]),
                    receipt_digest=cast(str, recovered["receipt_digest"]),
                )
                receipt = recovered
            except (SetupAuthorizationError, SetupLedgerError, _EffectFailure, OSError, ValueError):
                receipt = _receipt(
                    plan=validated_plan,
                    authorization=validated_authorization,
                    started_at=started_at,
                    completed_at=_completion_time(clock, started_at=started_at),
                    outcome="recovery_failed",
                    ledger_state="failed",
                    effects=effect_receipts,
                    protected=protected,
                    protected_after=all(probe(pid) for pid in protected),
                    recovery="failed",
                    effect_receipt_digest=failure_digest,
                )

    if receipt_path is not None:
        write_setup_receipt(receipt_path, receipt)
    return receipt
