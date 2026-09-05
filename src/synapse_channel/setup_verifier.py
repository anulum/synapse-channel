# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict end-to-end setup verification executor
"""Prove directed consumption and durable replay for one exact setup target."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import sqlite3
import stat
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.journal import EventKind
from synapse_channel.core.protocol import MessageType
from synapse_channel.setup_contract import SETUP_SCHEMA_VERSION, canonical_json, document_digest
from synapse_channel.setup_inspector import inspect_setup
from synapse_channel.setup_planner import setup_generation_from_inspection
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile
from synapse_channel.setup_verification import (
    SetupVerificationError,
    SetupVerificationLedger,
    default_verification_ledger_dir,
    validate_verification_authorization,
    validate_verification_plan,
)
from synapse_channel.shell_integration import pid_is_live_process

_COMMAND_TIMEOUT_SECONDS = 10.0
_VERIFY_TIMEOUT_SECONDS = 10.0
_LOCK_NAME = "setup-verify.lock"
_MAX_WATERMARK_SCAN = 10_000
_VERIFICATION_FAILURE_CODES = frozenset(
    {
        "verification_target_changed",
        "verification_protected_process_missing",
        "verification_canary_failed",
        "verification_restart_failed",
        "verification_replay_failed",
    }
)


@dataclass(frozen=True, slots=True)
class CanaryEvidence:
    """Redacted durable evidence for one directed canary and its waiter ACK."""

    message_seq: int
    chat_digest: str
    ack_event_seq: int
    ack_digest: str


class VerificationCommandRunner(Protocol):
    """Callable compatible with the fixed-argv systemd calls."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run one bounded command."""


class VerificationAgent(Protocol):
    """Minimal client surface used by the concrete verification adapter."""

    running: bool

    async def connect(self) -> None:
        """Connect and process frames until stopped."""

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Return whether registration completed before ``timeout``."""

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one message envelope."""

    async def request_history(self, limit: int | None = 20) -> None:
        """Request a bounded history snapshot."""


class VerificationAdapter(Protocol):
    """Runtime boundary needed by the strict verification transaction."""

    def current_hub_pid(self, systemctl: str) -> int | None:
        """Return the current managed hub PID."""

    async def issue_canary(
        self,
        *,
        target: dict[str, str],
        canary_id: str,
        database: Path,
        timeout: float,
    ) -> CanaryEvidence:
        """Send and observe one exact directed canary plus waiter consumption."""

    def restart_hub(self, systemctl: str) -> None:
        """Restart only the package-owned local hub unit."""

    async def prove_replay(
        self,
        *,
        target: dict[str, str],
        canary_id: str,
        database: Path,
        evidence: CanaryEvidence,
        old_pid: int,
        systemctl: str,
        timeout: float,
    ) -> int:
        """Return the new hub PID after proving durable replay through the hub."""


SetupInspectionRunner = Callable[..., Awaitable[dict[str, object]]]
PidProbe = Callable[[int], bool]
Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class VerificationProbeError(SynapseError, RuntimeError):
    """Stable failure raised by a concrete verification runtime adapter."""

    code = "verification_probe"

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _run(
    runner: VerificationCommandRunner,
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
        raise VerificationProbeError("verification_restart_failed") from exc


def _systemctl_pid(
    runner: VerificationCommandRunner,
    systemctl: str,
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
            "synapse-hub.service",
        ],
    )
    try:
        pid = int(completed.stdout.strip())
    except ValueError:
        return None
    return pid if completed.returncode == 0 and 1 < pid <= 2_147_483_647 else None


def _json_object(payload: object) -> dict[str, object]:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 1_048_576:
        raise VerificationProbeError("verification_replay_failed")
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise VerificationProbeError("verification_replay_failed") from exc
    if not isinstance(value, dict):
        raise VerificationProbeError("verification_replay_failed")
    return cast(dict[str, object], value)


def _event_digest(seq: int, kind: str, payload: dict[str, object]) -> str:
    return document_digest({"seq": seq, "kind": kind, "payload": payload})


def _open_read_only_store(path: Path) -> sqlite3.Connection:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError("unsafe event-store leaf")
        uri = f"file:{quote(str(path), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        verdict = connection.execute("PRAGMA quick_check").fetchone()
        if verdict != ("ok",):
            connection.close()
            raise sqlite3.DatabaseError("event-store integrity check failed")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise VerificationProbeError("verification_replay_failed") from exc


def _read_canary_evidence(
    database: Path,
    *,
    target: str,
    canary_id: str,
    message_seq: int,
) -> CanaryEvidence | None:
    expected_client_id = f"setup-{canary_id}"
    expected_payload = f"synapse-setup-canary:{canary_id}"
    with _open_read_only_store(database) as connection:
        try:
            chat_row = connection.execute(
                "SELECT seq, kind, payload FROM events WHERE seq = ?",
                (message_seq,),
            ).fetchone()
            if chat_row is None:
                return None
            seq, kind, raw_payload = chat_row
            if seq != message_seq or kind != EventKind.CHAT:
                raise VerificationProbeError("verification_replay_failed")
            chat = _json_object(raw_payload)
            if (
                chat.get("target") != target
                or chat.get("client_msg_id") != expected_client_id
                or chat.get("payload") != expected_payload
            ):
                raise VerificationProbeError("verification_replay_failed")
            rows = connection.execute(
                "SELECT seq, kind, payload FROM events "
                "WHERE seq > ? AND kind = ? ORDER BY seq LIMIT ?",
                (message_seq, EventKind.MAILBOX_WATERMARK, _MAX_WATERMARK_SCAN),
            ).fetchall()
        except sqlite3.Error as exc:
            raise VerificationProbeError("verification_replay_failed") from exc
    for ack_seq, ack_kind, raw_ack in rows:
        ack = _json_object(raw_ack)
        if (
            ack.get("identity") == target
            and ack.get("through_seq") == message_seq
            and ack.get("source") == "ack"
        ):
            return CanaryEvidence(
                message_seq=message_seq,
                chat_digest=_event_digest(message_seq, cast(str, kind), chat),
                ack_event_seq=cast(int, ack_seq),
                ack_digest=_event_digest(cast(int, ack_seq), cast(str, ack_kind), ack),
            )
    return None


class SystemdVerificationAdapter:
    """Linux systemd-user and local SQLite implementation of strict verification."""

    def __init__(
        self,
        *,
        runner: VerificationCommandRunner = subprocess.run,
        agent_factory: Callable[..., VerificationAgent] = SynapseAgent,
        probe: PidProbe = pid_is_live_process,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        token: str | None = None,
    ) -> None:
        self._runner = runner
        self._agent_factory = agent_factory
        self._probe = probe
        self._clock = clock
        self._sleep = sleeper
        self._token = token

    def current_hub_pid(self, systemctl: str) -> int | None:
        """Return the exact live MainPID for the managed hub."""
        pid = _systemctl_pid(self._runner, systemctl)
        return pid if pid is not None and self._probe(pid) else None

    async def issue_canary(
        self,
        *,
        target: dict[str, str],
        canary_id: str,
        database: Path,
        timeout: float,
    ) -> CanaryEvidence:
        """Send a directed canary and observe its durable chat and waiter ACK."""
        sender = f"{target['project']}/setup-verifier-{canary_id[:12]}"
        client_msg_id = f"setup-{canary_id}"
        receipts: list[dict[str, Any]] = []

        async def collect(data: dict[str, Any]) -> None:
            if (
                data.get("type") == MessageType.DELIVERY_RECEIPT
                and data.get("client_msg_id") == client_msg_id
            ):
                receipts.append(data)

        agent = self._agent_factory(
            sender,
            collect,
            uri=target["uri"],
            verbose=False,
            token=self._token,
            machine_identity=False,
        )
        task = asyncio.create_task(agent.connect())
        deadline = self._clock() + timeout
        try:
            if not await agent.wait_until_ready(timeout=timeout):
                raise VerificationProbeError("verification_canary_failed")
            await agent.send_message(
                MessageType.CHAT,
                target=target["identity"],
                payload=f"synapse-setup-canary:{canary_id}",
                receipt_requested=True,
                client_msg_id=client_msg_id,
                priority=False,
            )
            message_seq: int | None = None
            while self._clock() <= deadline:
                for receipt in receipts:
                    candidate = receipt.get("message_seq")
                    if (
                        receipt.get("delivered") is True
                        and isinstance(candidate, int)
                        and not isinstance(candidate, bool)
                    ):
                        message_seq = candidate
                        break
                if message_seq is not None:
                    evidence = _read_canary_evidence(
                        database,
                        target=target["identity"],
                        canary_id=canary_id,
                        message_seq=message_seq,
                    )
                    if evidence is not None:
                        return evidence
                await self._sleep(0.05)
            raise VerificationProbeError("verification_canary_failed")
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def restart_hub(self, systemctl: str) -> None:
        """Restart only ``synapse-hub.service`` through fixed argv."""
        completed = _run(
            self._runner,
            [systemctl, "--user", "restart", "--", "synapse-hub.service"],
        )
        if completed.returncode != 0:
            raise VerificationProbeError("verification_restart_failed")

    async def _connect_replay(
        self, agent: VerificationAgent, deadline: float
    ) -> asyncio.Task[None]:
        while (remaining := deadline - self._clock()) > 0:
            task = asyncio.create_task(agent.connect())
            ready = asyncio.create_task(agent.wait_until_ready(timeout=remaining))
            connected = False
            try:
                await asyncio.wait(
                    {task, ready}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if ready.done() and ready.result() and not task.done():
                    connected = True
                    return task
                if not task.done():
                    raise VerificationProbeError("verification_replay_failed")
                await task
            finally:
                ready.cancel()
                await asyncio.gather(ready, return_exceptions=True)
                if not connected:
                    agent.running = False
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await self._sleep(min(0.05, max(0.0, deadline - self._clock())))
        raise VerificationProbeError("verification_replay_failed")

    async def prove_replay(
        self,
        *,
        target: dict[str, str],
        canary_id: str,
        database: Path,
        evidence: CanaryEvidence,
        old_pid: int,
        systemctl: str,
        timeout: float,
    ) -> int:
        """Prove a new hub replayed the canary within one readiness/replay deadline.

        Retry ended connection attempts while the listener starts. Request only
        the exact canary, not surrounding history that may exceed frame limits.
        The durable chat and consumption digests must still match the evidence.

        Parameters
        ----------
        target : dict
            Exact hub URI, project and recipient identity from the verified plan.
        canary_id : str
            Identifier used when issuing the pre-restart canary.
        database : pathlib.Path
            Local durable event store to check after network replay.
        evidence : CanaryEvidence
            Original chat and consumption sequence numbers and digests.
        old_pid : int
            Hub process observed before the authorised restart.
        systemctl : str
            Validated systemctl executable for the exact managed hub unit.
        timeout : float
            Shared process-readiness, connection and replay budget in seconds.

        Returns
        -------
        int
            New hub PID whose connection replayed the matching canary.

        Raises
        ------
        VerificationProbeError
            The hub did not restart, replay expired or durable evidence changed.
        """
        deadline = self._clock() + timeout
        new_pid: int | None = None
        while self._clock() <= deadline:
            candidate = self.current_hub_pid(systemctl)
            if candidate is not None and candidate != old_pid:
                new_pid = candidate
                break
            await self._sleep(0.05)
        if new_pid is None:
            raise VerificationProbeError("verification_restart_failed")

        sender = f"{target['project']}/setup-replay-{canary_id[:12]}"
        replayed = asyncio.Event()

        async def collect(data: dict[str, Any]) -> None:
            if data.get("type") != MessageType.HISTORY_SNAPSHOT:
                return
            history = data.get("history")
            if not isinstance(history, list):
                return
            if any(
                isinstance(item, dict)
                and item.get("client_msg_id") == f"setup-{canary_id}"
                and item.get("target") == target["identity"]
                for item in history
            ):
                replayed.set()

        agent = self._agent_factory(
            sender,
            collect,
            uri=target["uri"],
            verbose=False,
            token=self._token,
            machine_identity=False,
        )
        task = await self._connect_replay(agent, deadline)
        try:
            await agent.send_message(
                MessageType.HISTORY_REQUEST,
                target="System",
                payload="history",
                limit=1,
                history_client_msg_id=f"setup-{canary_id}",
                history_target=target["identity"],
            )
            remaining = max(0.0, deadline - self._clock())
            try:
                await asyncio.wait_for(replayed.wait(), timeout=remaining)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise VerificationProbeError("verification_replay_failed") from exc
        finally:
            agent.running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        observed = _read_canary_evidence(
            database,
            target=target["identity"],
            canary_id=canary_id,
            message_seq=evidence.message_seq,
        )
        if observed != evidence:
            raise VerificationProbeError("verification_replay_failed")
        return new_pid


@contextlib.contextmanager
def _host_lock(directory: Path) -> Any:
    path = directory / _LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError(  # pragma: no cover - post-open platform/FS invariant
                "unsafe verification lock"
            )
        os.fchmod(descriptor, 0o600)
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError) as exc:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise SetupVerificationError("verification_lock_unavailable") from exc
    try:
        yield
    finally:
        os.close(descriptor)


def _protected_pids(
    requested: Sequence[int],
    *,
    restart_pid: int,
    probe: PidProbe,
) -> tuple[int, ...]:
    values = tuple(dict.fromkeys((os.getppid(), *requested)))
    if any(
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or not 1 < pid <= 2_147_483_647
        or pid == restart_pid
        or not probe(pid)
        for pid in values
    ):
        raise SetupVerificationError("verification_protected_process_missing")
    return values


def _receipt(
    *,
    plan: dict[str, object],
    authorization: dict[str, object],
    started_at: int,
    completed_at: int,
    outcome: str,
    ledger_state: str,
    canary_id: str,
    evidence: CanaryEvidence | None,
    hub_pid_after: int | None,
    protected: tuple[int, ...],
    protected_after: bool,
    checks: list[dict[str, str]],
    failure_code: str | None,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "verification_receipt",
        "profile": plan["profile"],
        "profile_version": plan["profile_version"],
        "verification_plan_digest": plan["verification_plan_digest"],
        "verification_authorization_digest": authorization["verification_authorization_digest"],
        "plan_digest": plan["plan_digest"],
        "authorization_digest": plan["authorization_digest"],
        "application_receipt_digest": plan["application_receipt_digest"],
        "target": plan["target"],
        "generation": plan["generation"],
        "started_at": started_at,
        "completed_at": completed_at,
        "outcome": outcome,
        "ledger_state": ledger_state,
        "canary_id": canary_id,
        "message_seq": None if evidence is None else evidence.message_seq,
        "chat_digest": None if evidence is None else evidence.chat_digest,
        "ack_event_seq": None if evidence is None else evidence.ack_event_seq,
        "ack_digest": None if evidence is None else evidence.ack_digest,
        "hub_pid_before": plan["current_hub_pid"],
        "hub_pid_after": hub_pid_after,
        "protected_processes": [
            {"pid": pid, "before_alive": True, "after_alive": protected_after} for pid in protected
        ],
        "checks": checks,
        "failure_code": failure_code,
    }
    document["receipt_digest"] = document_digest(document)
    return document


def _validate_receipt_path(path: Path) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise SetupVerificationError("verification_receipt_unavailable")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise SetupVerificationError("verification_receipt_unavailable")


def write_verification_receipt(path: str | Path, receipt: dict[str, object]) -> None:
    """Atomically write one owner-only strict-verification receipt."""
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
        raise SetupVerificationError("verification_receipt_unavailable", receipt=receipt) from exc


def _check_rows(failed: str | None) -> list[dict[str, str]]:
    ordered = [
        ("directed_canary_delivery", {"verification_canary_failed"}),
        ("exact_waiter_consumption", {"verification_canary_failed"}),
        (
            "durable_restart_replay",
            {"verification_restart_failed", "verification_replay_failed"},
        ),
        ("strict_reinspection", {"verification_target_changed"}),
        ("protected_process_continuity", {"verification_protected_process_missing"}),
    ]
    if failed is None:
        return [{"id": check, "status": "pass"} for check, _code in ordered]
    rows: list[dict[str, str]] = []
    failure_seen = False
    for check, codes in ordered:
        if not failure_seen and failed in codes:
            rows.append({"id": check, "status": "fail"})
            failure_seen = True
        else:
            rows.append({"id": check, "status": "not_run" if failure_seen else "pass"})
    if not failure_seen:
        rows = [{"id": check, "status": "not_run"} for check, _codes in ordered]
    return rows


async def verify_setup(
    verification_plan: dict[str, object],
    verification_authorization: dict[str, object],
    *,
    confirm_digest: str,
    protected_pids: Sequence[int] = (),
    receipt_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    ledger_directory: str | Path | None = None,
    adapter: VerificationAdapter | None = None,
    inspection_runner: SetupInspectionRunner = inspect_setup,
    probe: PidProbe = pid_is_live_process,
    wall_clock: Clock = time.time,
    monotonic: Clock = time.monotonic,
    sleeper: Sleeper = asyncio.sleep,
) -> dict[str, object]:
    """Consume one exact authorization and prove strict setup operation end to end."""
    plan = validate_verification_plan(verification_plan)
    if confirm_digest != plan["verification_plan_digest"]:
        raise SetupVerificationError("digest_mismatch")
    try:
        started_at = int(wall_clock())
    except (OverflowError, TypeError, ValueError) as exc:
        raise SetupVerificationError("verification_target_changed") from exc
    if started_at < 0:
        raise SetupVerificationError("verification_target_changed")
    authorization = validate_verification_authorization(
        plan,
        verification_authorization,
        now=started_at,
    )
    if platform.system() != "Linux":  # pragma: no cover - platform-specific adapter refusal
        raise SetupVerificationError("application_platform_unsupported")
    if receipt_path is not None:
        _validate_receipt_path(Path(receipt_path))
    environment = os.environ if env is None else env
    home = Path(environment.get("HOME", str(Path.home())))
    if not home.is_absolute():
        raise SetupVerificationError("verification_target_changed")
    ledger_dir = (
        default_verification_ledger_dir(env=environment)
        if ledger_directory is None
        else Path(ledger_directory)
    )
    target = cast(dict[str, str], plan["target"])
    generation = cast(dict[str, str], plan["generation"])
    restart_pid = cast(int, plan["current_hub_pid"])
    runtime = adapter or SystemdVerificationAdapter(
        probe=probe,
        token=environment.get("SYNAPSE_TOKEN") or None,
    )
    profile = cast(SetupProfile, get_setup_profile(cast(str, plan["profile"])))
    canary_id = sha256(
        cast(str, authorization["verification_authorization_digest"]).encode("ascii")
    ).hexdigest()[:32]
    evidence: CanaryEvidence | None = None
    hub_pid_after: int | None = None
    failure_code: str | None = None
    protected: tuple[int, ...] = ()

    with SetupVerificationLedger(ledger_dir) as ledger, _host_lock(ledger.directory):
        if runtime.current_hub_pid(generation["service_manager_executable"]) != restart_pid:
            raise SetupVerificationError("verification_target_changed")
        protected = _protected_pids(protected_pids, restart_pid=restart_pid, probe=probe)
        ledger.reserve(plan, authorization, now=started_at)
        try:
            database = home / "synapse" / "hub.db"
            evidence = await runtime.issue_canary(
                target=target,
                canary_id=canary_id,
                database=database,
                timeout=_VERIFY_TIMEOUT_SECONDS,
            )
            if not all(probe(pid) for pid in protected):
                raise VerificationProbeError("verification_protected_process_missing")
            runtime.restart_hub(generation["service_manager_executable"])
            hub_pid_after = await runtime.prove_replay(
                target=target,
                canary_id=canary_id,
                database=database,
                evidence=evidence,
                old_pid=restart_pid,
                systemctl=generation["service_manager_executable"],
                timeout=_VERIFY_TIMEOUT_SECONDS,
            )
            deadline = monotonic() + _VERIFY_TIMEOUT_SECONDS
            fresh: dict[str, object] | None = None
            inspection_env = {
                **environment,
                "SYN_PROJECT": target["project"],
                "SYN_IDENTITY": target["identity"],
            }
            while monotonic() <= deadline:
                candidate = await inspection_runner(
                    profile,
                    uri=target["uri"],
                    project=None,
                    agent_id=None,
                    env=inspection_env,
                )
                try:
                    candidate_generation = setup_generation_from_inspection(candidate)
                except (AttributeError, KeyError, TypeError, ValueError):
                    candidate_generation = {}
                if (
                    candidate.get("ready") is True
                    and candidate.get("target") == target
                    and candidate_generation == generation
                    and runtime.current_hub_pid(generation["service_manager_executable"])
                    == hub_pid_after
                ):
                    fresh = candidate
                    break
                await sleeper(0.05)
            if fresh is None:
                raise VerificationProbeError("verification_target_changed")
            if not all(probe(pid) for pid in protected):
                raise VerificationProbeError("verification_protected_process_missing")
            receipt = _receipt(
                plan=plan,
                authorization=authorization,
                started_at=started_at,
                completed_at=max(started_at, int(wall_clock())),
                outcome="verified",
                ledger_state="verified",
                canary_id=canary_id,
                evidence=evidence,
                hub_pid_after=hub_pid_after,
                protected=protected,
                protected_after=True,
                checks=_check_rows(None),
                failure_code=None,
            )
            ledger.finish(
                cast(str, authorization["verification_authorization_digest"]),
                outcome="verified",
                receipt_digest=cast(str, receipt["receipt_digest"]),
            )
        except (VerificationProbeError, OSError, ValueError) as exc:
            failure_code = (
                exc.code
                if isinstance(exc, VerificationProbeError)
                and exc.code in _VERIFICATION_FAILURE_CODES
                else "verification_replay_failed"
            )
            protected_after = all(probe(pid) for pid in protected)
            if not protected_after:
                failure_code = "verification_protected_process_missing"
            try:
                completed_at = max(started_at, int(wall_clock()))
            except (OverflowError, TypeError, ValueError):
                completed_at = started_at
            receipt = _receipt(
                plan=plan,
                authorization=authorization,
                started_at=started_at,
                completed_at=completed_at,
                outcome="failed",
                ledger_state="failed",
                canary_id=canary_id,
                evidence=evidence,
                hub_pid_after=hub_pid_after,
                protected=protected,
                protected_after=protected_after,
                checks=_check_rows(failure_code),
                failure_code=failure_code,
            )
            ledger.finish(
                cast(str, authorization["verification_authorization_digest"]),
                outcome="failed",
                receipt_digest=cast(str, receipt["receipt_digest"]),
            )
    if receipt_path is not None:
        write_verification_receipt(receipt_path, receipt)
    return receipt
