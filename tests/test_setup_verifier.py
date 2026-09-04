# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict setup verification runtime tests

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

import synapse_channel.setup_verifier as setup_verifier
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.journal import EventKind
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.wake_capability import WAKE_PASSIVE
from synapse_channel.setup_contract import setup_schema
from synapse_channel.setup_verification import SetupVerificationError
from synapse_channel.setup_verifier import (
    CanaryEvidence,
    SystemdVerificationAdapter,
    VerificationAgent,
    VerificationProbeError,
    verify_setup,
    write_verification_receipt,
)
from synapse_channel.shell_integration import pid_is_live_process
from test_setup_verification import inspection, setup_documents


class RecordingAdapter:
    """Deterministic public adapter for transaction-state tests."""

    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.restarted = False
        self.calls: list[str] = []

    def current_hub_pid(self, _systemctl: str) -> int | None:
        self.calls.append("pid")
        return 9876 if self.restarted else 4321

    async def issue_canary(
        self,
        *,
        target: dict[str, str],
        canary_id: str,
        database: Path,
        timeout: float,
    ) -> CanaryEvidence:
        self.calls.append("canary")
        assert target["identity"] == "DEMO/codex-one"
        assert canary_id and database.name == "hub.db" and timeout == 10.0
        if self.failure in {"verification_canary_failed", "unknown_verification_failure"}:
            raise VerificationProbeError(self.failure)
        return CanaryEvidence(10, "a" * 64, 12, "b" * 64)

    def restart_hub(self, _systemctl: str) -> None:
        self.calls.append("restart")
        if self.failure == "verification_restart_failed":
            raise VerificationProbeError(self.failure)
        self.restarted = True

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
        self.calls.append("replay")
        assert target["project"] == "DEMO"
        assert canary_id and database.name == "hub.db"
        assert evidence.message_seq == 10 and old_pid == 4321
        assert systemctl == "/usr/bin/true" and timeout == 10.0
        if self.failure == "verification_replay_failed":
            raise VerificationProbeError(self.failure)
        return 9876


def inspection_runner(document: dict[str, object]) -> Callable[..., Awaitable[dict[str, object]]]:
    async def run(*_args: object, **kwargs: object) -> dict[str, object]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["SYN_PROJECT"] == "DEMO"
        assert environment["SYN_IDENTITY"] == "DEMO/codex-one"
        return document

    return run


def run_verification(
    tmp_path: Path,
    *,
    adapter: RecordingAdapter,
    inspection_document: dict[str, object] | None = None,
    probe: Callable[[int], bool] = lambda _pid: True,
    receipt: Path | None = None,
) -> dict[str, object]:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return asyncio.run(
        verify_setup(
            verification_plan,
            verification_authorization,
            confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
            protected_pids=(7777,),
            receipt_path=receipt,
            env={"HOME": str(home)},
            ledger_directory=tmp_path / "ledger",
            adapter=adapter,
            inspection_runner=inspection_runner(
                inspection(waiter="pass", hub_pid=9876)
                if inspection_document is None
                else inspection_document
            ),
            probe=probe,
            wall_clock=lambda: 201.0,
        )
    )


def test_verify_transaction_binds_all_evidence_and_is_schema_valid(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    target = tmp_path / "verification.json"
    adapter = RecordingAdapter()
    receipt = run_verification(tmp_path, adapter=adapter, receipt=target)
    assert receipt["outcome"] == "verified"
    assert receipt["message_seq"] == 10
    assert receipt["hub_pid_before"] == 4321
    assert receipt["hub_pid_after"] == 9876
    assert receipt["failure_code"] is None
    assert adapter.calls == ["pid", "canary", "restart", "replay", "pid"]
    assert json.loads(target.read_text(encoding="utf-8")) == receipt
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    jsonschema.validate(receipt, setup_schema())


@pytest.mark.parametrize(
    "failure",
    [
        "verification_canary_failed",
        "verification_restart_failed",
        "verification_replay_failed",
    ],
)
def test_verify_transaction_records_failures_without_claiming_recovery(
    tmp_path: Path,
    failure: str,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    receipt = run_verification(tmp_path, adapter=RecordingAdapter(failure=failure))
    assert receipt["outcome"] == "failed"
    assert receipt["ledger_state"] == "failed"
    assert receipt["failure_code"] == failure
    assert any(item["status"] == "fail" for item in cast(list[dict[str, str]], receipt["checks"]))
    jsonschema.validate(receipt, setup_schema())


def test_verify_requires_exact_digest_platform_pid_and_live_protected_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    kwargs: dict[str, object] = {
        "env": {"HOME": str(tmp_path)},
        "ledger_directory": tmp_path / "ledger",
        "adapter": RecordingAdapter(),
        "inspection_runner": inspection_runner(inspection(waiter="pass", hub_pid=9876)),
        "probe": lambda _pid: True,
        "wall_clock": lambda: 201.0,
    }
    with pytest.raises(SetupVerificationError, match="digest_mismatch"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                confirm_digest="a" * 64,
                **kwargs,  # type: ignore[arg-type]
            )
        )
    monkeypatch.setattr("synapse_channel.setup_verifier.platform.system", lambda: "Darwin")
    with pytest.raises(SetupVerificationError, match="application_platform_unsupported"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
                **kwargs,  # type: ignore[arg-type]
            )
        )
    monkeypatch.setattr("synapse_channel.setup_verifier.platform.system", lambda: "Linux")
    wrong = RecordingAdapter()
    wrong.restarted = True
    kwargs["adapter"] = wrong
    with pytest.raises(SetupVerificationError, match="verification_target_changed"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
                **kwargs,  # type: ignore[arg-type]
            )
        )
    kwargs["adapter"] = RecordingAdapter()
    kwargs["probe"] = lambda _pid: False
    with pytest.raises(SetupVerificationError, match="verification_protected_process_missing"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
                **kwargs,  # type: ignore[arg-type]
            )
        )


def test_verify_records_reinspection_and_late_process_failure(tmp_path: Path) -> None:
    not_ready = inspection(waiter="fail", hub_pid=9876)
    receipt = run_verification(
        tmp_path / "reinspect",
        adapter=RecordingAdapter(),
        inspection_document=not_ready,
    )
    assert receipt["failure_code"] == "verification_target_changed"

    probes = iter([True, True, True, False, False])

    def dies(_pid: int) -> bool:
        return next(probes, False)

    receipt = run_verification(
        tmp_path / "process",
        adapter=RecordingAdapter(),
        probe=dies,
    )
    assert receipt["failure_code"] == "verification_protected_process_missing"


def test_verification_authorization_is_single_use(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    first = run_verification(tmp_path, adapter=adapter)
    assert first["outcome"] == "verified"
    with pytest.raises(SetupVerificationError, match="verification_authorization_replayed"):
        run_verification(tmp_path, adapter=RecordingAdapter())


def test_verification_receipt_writer_refuses_unsafe_targets(tmp_path: Path) -> None:
    receipt: dict[str, object] = {"document_kind": "verification_receipt"}
    with pytest.raises(SetupVerificationError, match="verification_receipt_unavailable"):
        write_verification_receipt(tmp_path / "missing" / "receipt.json", receipt)
    regular = tmp_path / "regular"
    regular.write_text("old", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(regular)
    with pytest.raises(SetupVerificationError, match="verification_receipt_unavailable"):
        write_verification_receipt(link, receipt)

    output = tmp_path / "receipt.json"
    write_verification_receipt(output, receipt)
    write_verification_receipt(output, receipt)


class PidRunner:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.fail_restart = False
        self.commands: list[list[str]] = []

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check and timeout == 10.0
        self.commands.append(args)
        if "show" in args:
            return subprocess.CompletedProcess(args, 0, f"{self.pid}\n", "")
        return subprocess.CompletedProcess(args, 1 if self.fail_restart else 0, "", "")


def test_systemd_adapter_uses_fixed_commands_and_live_pid() -> None:
    runner = PidRunner(4321)
    adapter = SystemdVerificationAdapter(runner=runner, probe=lambda pid: pid == 4321)
    assert adapter.current_hub_pid("/usr/bin/systemctl") == 4321
    adapter.restart_hub("/usr/bin/systemctl")
    assert runner.commands[-1] == [
        "/usr/bin/systemctl",
        "--user",
        "restart",
        "--",
        "synapse-hub.service",
    ]
    runner.fail_restart = True
    with pytest.raises(VerificationProbeError, match="verification_restart_failed"):
        adapter.restart_hub("/usr/bin/systemctl")
    runner.pid = 1
    assert adapter.current_hub_pid("/usr/bin/systemctl") is None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


async def _await_listening(port: int) -> None:
    for _ in range(150):
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError("disposable hub did not listen")


async def _close_agent(agent: SynapseAgent, task: asyncio.Task[None]) -> None:
    agent.running = False
    if agent.connection is not None:
        await agent.connection.close()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _start_disposable_hub(*, port: int, database: Path) -> subprocess.Popen[bytes]:
    executable = Path(sys.executable).with_name("synapse")
    isolated_home = database.parent / "home"
    isolated_home.mkdir(mode=0o700, exist_ok=True)
    return subprocess.Popen(  # noqa: S603 - fixed local test executable and argv
        [
            str(executable),
            "hub",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(database),
            "--insecure-plaintext-at-rest",
            "--identity-pins",
            "",
        ],
        env={**os.environ, "HOME": str(isolated_home)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _start_ready_disposable_hub(
    *,
    database: Path,
) -> tuple[int, subprocess.Popen[bytes]]:
    for _attempt in range(5):
        port = _free_port()
        process = _start_disposable_hub(port=port, database=database)
        try:
            await _await_listening(port)
        except TimeoutError:
            _stop_process(process)
            continue
        if process.poll() is None:
            return port, process
        _stop_process(process)
    raise RuntimeError("could not allocate an isolated disposable hub port")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


async def test_systemd_adapter_real_canary_consumption_and_restart_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "hub.db"
    port, first_hub = await _start_ready_disposable_hub(database=database)
    uri = f"ws://127.0.0.1:{port}"
    target = {"uri": uri, "project": "DEMO", "identity": "DEMO/verified"}
    protected = subprocess.Popen(  # noqa: S603 - fixed interpreter and inert sleep script
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert pid_is_live_process(protected.pid)
        waiter = SynapseAgent(
            "DEMO/verified-rx",
            uri=uri,
            verbose=False,
            mailbox=True,
            mailbox_for="DEMO/verified",
            wake_capability=WAKE_PASSIVE,
            machine_identity=False,
        )
        waiter_task = asyncio.create_task(waiter.connect())
        assert await waiter.wait_until_ready(3.0)
        runner = PidRunner(first_hub.pid)
        adapter = SystemdVerificationAdapter(runner=runner, probe=pid_is_live_process)
        evidence = await adapter.issue_canary(
            target=target,
            canary_id="0123456789abcdef0123456789abcdef",
            database=database,
            timeout=3.0,
        )
        assert evidence.message_seq > 0
        await _close_agent(waiter, waiter_task)
        old_pid = first_hub.pid
        _stop_process(first_hub)

        replay_hub = _start_disposable_hub(port=port, database=database)
        try:
            await _await_listening(port)
            runner.pid = replay_hub.pid
            new_pid = await adapter.prove_replay(
                target=target,
                canary_id="0123456789abcdef0123456789abcdef",
                database=database,
                evidence=evidence,
                old_pid=old_pid,
                systemctl="/usr/bin/systemctl",
                timeout=3.0,
            )
            assert new_pid == replay_hub.pid
            assert new_pid != old_pid
            assert pid_is_live_process(protected.pid)
        finally:
            _stop_process(replay_hub)
    finally:
        _stop_process(first_hub)
        _stop_process(protected)


def test_systemd_adapter_replay_refuses_unchanged_pid(tmp_path: Path) -> None:
    runner = PidRunner(4321)
    ticks = iter([0.0, 0.0, 2.0])

    async def no_sleep(_delay: float) -> None:
        return None

    adapter = SystemdVerificationAdapter(
        runner=runner,
        probe=lambda _pid: True,
        clock=lambda: next(ticks, 2.0),
        sleeper=no_sleep,
    )
    with pytest.raises(VerificationProbeError, match="verification_restart_failed"):
        asyncio.run(
            adapter.prove_replay(
                target={
                    "uri": "ws://127.0.0.1:1",
                    "project": "DEMO",
                    "identity": "DEMO/x",
                },
                canary_id="0" * 32,
                database=tmp_path / "missing.db",
                evidence=CanaryEvidence(1, "a" * 64, 2, "b" * 64),
                old_pid=4321,
                systemctl="/usr/bin/systemctl",
                timeout=1.0,
            )
        )


class ScriptedAgent:
    """Small protocol double for public adapter refusal paths."""

    def __init__(
        self,
        _identity: str,
        handler: Callable[[dict[str, object]], Awaitable[None]],
        *,
        ready: bool,
        history: object = None,
        receipts: list[dict[str, object]] | None = None,
        **_kwargs: object,
    ) -> None:
        self.handler = handler
        self.ready = ready
        self.history = history
        self.receipts = [] if receipts is None else receipts
        self.running = True

    async def connect(self) -> None:
        await asyncio.sleep(3600)

    async def wait_until_ready(self, timeout: float) -> bool:
        assert timeout > 0
        return self.ready

    async def send_message(self, *_args: object, **_kwargs: object) -> None:
        for receipt in self.receipts:
            await self.handler(receipt)

    async def request_history(self, limit: int | None = 20) -> None:
        assert limit == 1000
        await self.handler({"type": MessageType.HISTORY_SNAPSHOT, "history": self.history})


def _scripted_factory(
    *,
    ready: bool,
    history: object = None,
    receipts: list[dict[str, object]] | None = None,
) -> Callable[..., VerificationAgent]:
    def factory(*args: object, **kwargs: object) -> VerificationAgent:
        return cast(
            VerificationAgent,
            ScriptedAgent(
                cast(str, args[0]),
                cast(Callable[[dict[str, object]], Awaitable[None]], args[1]),
                ready=ready,
                history=history,
                receipts=receipts,
                **kwargs,
            ),
        )

    return factory


def _event_database(
    path: Path,
    *,
    canary_id: str,
    chat_kind: str = EventKind.CHAT,
    chat_payload: object | None = None,
    ack_payload: object | None = None,
) -> None:
    expected_chat = {
        "target": "DEMO/x",
        "client_msg_id": f"setup-{canary_id}",
        "payload": f"synapse-setup-canary:{canary_id}",
    }
    expected_ack = {"identity": "DEMO/x", "through_seq": 1, "source": "ack"}
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE events (seq INTEGER, kind TEXT, payload TEXT)")
        connection.execute(
            "INSERT INTO events VALUES (1, ?, ?)",
            (chat_kind, json.dumps(expected_chat if chat_payload is None else chat_payload)),
        )
        if ack_payload is not False:
            connection.execute(
                "INSERT INTO events VALUES (2, ?, ?)",
                (
                    EventKind.MAILBOX_WATERMARK,
                    json.dumps(expected_ack if ack_payload is None else ack_payload),
                ),
            )


@pytest.mark.parametrize("payload", [7, "[1]", "{", "x" * 1_048_577])
def test_adapter_json_decoder_rejects_unbounded_or_non_object_payload(payload: object) -> None:
    with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
        setup_verifier._json_object(payload)


def test_adapter_event_store_evidence_failures_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_id = "0" * 32
    missing_chat = tmp_path / "missing-chat.db"
    with sqlite3.connect(missing_chat) as connection:
        connection.execute("CREATE TABLE events (seq INTEGER, kind TEXT, payload TEXT)")
    assert (
        setup_verifier._read_canary_evidence(
            missing_chat,
            target="DEMO/x",
            canary_id=canary_id,
            message_seq=1,
        )
        is None
    )

    cases = (
        ("wrong-kind.db", "wrong", None, None),
        ("wrong-chat.db", EventKind.CHAT, {"target": "DEMO/other"}, None),
        (
            "wrong-ack.db",
            EventKind.CHAT,
            None,
            {"identity": "DEMO/other", "through_seq": 1, "source": "ack"},
        ),
    )
    for name, kind, chat, ack in cases:
        path = tmp_path / name
        _event_database(
            path,
            canary_id=canary_id,
            chat_kind=kind,
            chat_payload=chat,
            ack_payload=ack,
        )
        if name == "wrong-ack.db":
            assert (
                setup_verifier._read_canary_evidence(
                    path,
                    target="DEMO/x",
                    canary_id=canary_id,
                    message_seq=1,
                )
                is None
            )
        else:
            with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
                setup_verifier._read_canary_evidence(
                    path,
                    target="DEMO/x",
                    canary_id=canary_id,
                    message_seq=1,
                )

    no_table = tmp_path / "no-table.db"
    sqlite3.connect(no_table).close()
    with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
        setup_verifier._read_canary_evidence(
            no_table,
            target="DEMO/x",
            canary_id=canary_id,
            message_seq=1,
        )
    for unsafe in (tmp_path / "absent.db", tmp_path):
        with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
            setup_verifier._open_read_only_store(unsafe)

    class BadCheck:
        closed = False

        def execute(self, _sql: str) -> BadCheck:
            return self

        def fetchone(self) -> tuple[str]:
            return ("damaged",)

        def close(self) -> None:
            self.closed = True

    bad = BadCheck()
    regular = tmp_path / "regular.db"
    regular.touch()
    monkeypatch.setattr(
        "synapse_channel.setup_verifier.sqlite3.connect",
        lambda *_args, **_kwargs: bad,
    )
    with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
        setup_verifier._open_read_only_store(regular)
    assert bad.closed


def test_adapter_command_and_canary_refusals_are_bounded(tmp_path: Path) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("unavailable")

    adapter = SystemdVerificationAdapter(runner=unavailable)
    with pytest.raises(VerificationProbeError, match="verification_restart_failed"):
        adapter.current_hub_pid("/usr/bin/systemctl")

    runner = PidRunner(4321)
    runner.pid = 4321
    runner_output = subprocess.CompletedProcess([], 0, "not-a-pid", "")
    unparsable = SystemdVerificationAdapter(runner=lambda *_args, **_kwargs: runner_output)
    assert unparsable.current_hub_pid("/usr/bin/systemctl") is None

    target = {"uri": "ws://127.0.0.1:1", "project": "DEMO", "identity": "DEMO/x"}
    captured: dict[str, object] = {}
    base_factory = _scripted_factory(ready=False)

    def capturing_factory(*args: object, **kwargs: object) -> VerificationAgent:
        captured.update(kwargs)
        return base_factory(*args, **kwargs)

    not_ready = SystemdVerificationAdapter(
        agent_factory=capturing_factory,
        token="existing-secret",
    )
    with pytest.raises(VerificationProbeError, match="verification_canary_failed"):
        asyncio.run(
            not_ready.issue_canary(
                target=target,
                canary_id="0" * 32,
                database=tmp_path / "missing.db",
                timeout=0.1,
            )
        )
    assert captured["token"] == "existing-secret"
    assert captured["machine_identity"] is False

    ticks = iter([0.0, 0.0, 2.0])

    async def no_sleep(_delay: float) -> None:
        return None

    timed_out = SystemdVerificationAdapter(
        agent_factory=_scripted_factory(
            ready=True,
            receipts=[{"type": MessageType.DELIVERY_RECEIPT, "delivered": False}],
        ),
        clock=lambda: next(ticks, 2.0),
        sleeper=no_sleep,
    )
    with pytest.raises(VerificationProbeError, match="verification_canary_failed"):
        asyncio.run(
            timed_out.issue_canary(
                target=target,
                canary_id="0" * 32,
                database=tmp_path / "missing.db",
                timeout=1.0,
            )
        )


def test_adapter_replay_refuses_bad_history_timeout_and_changed_evidence(tmp_path: Path) -> None:
    target = {"uri": "ws://127.0.0.1:1", "project": "DEMO", "identity": "DEMO/x"}
    evidence = CanaryEvidence(1, "a" * 64, 2, "b" * 64)
    runner = PidRunner(9876)

    for factory in (
        _scripted_factory(ready=False),
        _scripted_factory(ready=True, history="bad"),
        _scripted_factory(ready=True, history=[{}]),
    ):
        adapter = SystemdVerificationAdapter(
            runner=runner,
            agent_factory=factory,
            probe=lambda _pid: True,
        )
        with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
            asyncio.run(
                adapter.prove_replay(
                    target=target,
                    canary_id="0" * 32,
                    database=tmp_path / "missing.db",
                    evidence=evidence,
                    old_pid=4321,
                    systemctl="/usr/bin/systemctl",
                    timeout=0.1,
                )
            )

    database = tmp_path / "events.db"
    _event_database(database, canary_id="0" * 32)
    adapter = SystemdVerificationAdapter(
        runner=runner,
        agent_factory=_scripted_factory(
            ready=True,
            history=[{"client_msg_id": f"setup-{'0' * 32}", "target": "DEMO/x"}],
        ),
        probe=lambda _pid: True,
    )
    with pytest.raises(VerificationProbeError, match="verification_replay_failed"):
        asyncio.run(
            adapter.prove_replay(
                target=target,
                canary_id="0" * 32,
                database=database,
                evidence=evidence,
                old_pid=4321,
                systemctl="/usr/bin/systemctl",
                timeout=1.0,
            )
        )


def test_verify_refuses_unsafe_lock_clock_home_and_receipt_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    digest = cast(str, verification_plan["verification_plan_digest"])
    base: dict[str, object] = {
        "confirm_digest": digest,
        "env": {"HOME": str(tmp_path / "home")},
        "ledger_directory": tmp_path / "ledger",
        "adapter": RecordingAdapter(),
        "inspection_runner": inspection_runner(inspection(waiter="pass", hub_pid=9876)),
        "probe": lambda _pid: True,
    }
    (tmp_path / "home").mkdir()
    for clock in (lambda: float("nan"), lambda: -1.0):
        with pytest.raises(SetupVerificationError, match="verification_target_changed"):
            asyncio.run(
                verify_setup(
                    verification_plan,
                    verification_authorization,
                    wall_clock=clock,
                    **base,  # type: ignore[arg-type]
                )
            )

    relative = {**base, "env": {"HOME": "relative"}}
    with pytest.raises(SetupVerificationError, match="verification_target_changed"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                wall_clock=lambda: 201.0,
                **relative,  # type: ignore[arg-type]
            )
        )

    ledger = tmp_path / "lock-ledger"
    ledger.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.touch()
    (ledger / "setup-verify.lock").symlink_to(victim)
    locked = {**base, "ledger_directory": ledger}
    with pytest.raises(SetupVerificationError, match="verification_lock_unavailable"):
        asyncio.run(
            verify_setup(
                verification_plan,
                verification_authorization,
                wall_clock=lambda: 201.0,
                **locked,  # type: ignore[arg-type]
            )
        )

    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        "synapse_channel.setup_verifier.tempfile.mkstemp",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    carried: dict[str, object] = {"outcome": "failed"}
    with pytest.raises(SetupVerificationError, match="verification_receipt_unavailable") as error:
        write_verification_receipt(receipt, carried)
    assert error.value.receipt is carried


def test_verify_covers_default_construction_unknown_failure_and_failure_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _authorization, _receipt, verification_plan, verification_authorization = (
        setup_documents()
    )
    monkeypatch.setattr(
        setup_verifier,
        "SystemdVerificationAdapter",
        lambda **_kwargs: RecordingAdapter(failure="unknown_verification_failure"),
    )
    clocks = iter([201.0, float("nan")])
    receipt = asyncio.run(
        verify_setup(
            verification_plan,
            verification_authorization,
            confirm_digest=cast(str, verification_plan["verification_plan_digest"]),
            env={"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state")},
            adapter=None,
            inspection_runner=inspection_runner(inspection(waiter="pass", hub_pid=9876)),
            probe=lambda _pid: True,
            wall_clock=lambda: next(clocks),
        )
    )
    assert receipt["failure_code"] == "verification_replay_failed"
    checks = cast(list[dict[str, str]], receipt["checks"])
    assert checks[2]["status"] == "fail"
    assert receipt["completed_at"] == 201
    assert all(
        item["status"] == "not_run"
        for item in setup_verifier._check_rows("unknown_verification_failure")
    )


def test_adapter_canary_skips_invalid_receipts_and_waits_for_durable_ack(tmp_path: Path) -> None:
    canary_id = "0" * 32
    target = {"uri": "ws://127.0.0.1:1", "project": "DEMO", "identity": "DEMO/x"}
    database = tmp_path / "events.db"
    _event_database(database, canary_id=canary_id)
    adapter = SystemdVerificationAdapter(
        agent_factory=_scripted_factory(
            ready=True,
            receipts=[
                {
                    "type": MessageType.DELIVERY_RECEIPT,
                    "client_msg_id": f"setup-{canary_id}",
                    "delivered": False,
                    "message_seq": "bad",
                },
                {
                    "type": MessageType.DELIVERY_RECEIPT,
                    "client_msg_id": f"setup-{canary_id}",
                    "delivered": True,
                    "message_seq": 1,
                },
            ],
        )
    )
    evidence = asyncio.run(
        adapter.issue_canary(
            target=target,
            canary_id=canary_id,
            database=database,
            timeout=1.0,
        )
    )
    assert evidence.message_seq == 1

    empty = tmp_path / "empty.db"
    with sqlite3.connect(empty) as connection:
        connection.execute("CREATE TABLE events (seq INTEGER, kind TEXT, payload TEXT)")
    ticks = iter([0.0, 0.0, 2.0])

    async def no_sleep(_delay: float) -> None:
        return None

    adapter = SystemdVerificationAdapter(
        agent_factory=_scripted_factory(
            ready=True,
            receipts=[
                {
                    "type": MessageType.DELIVERY_RECEIPT,
                    "client_msg_id": f"setup-{canary_id}",
                    "delivered": True,
                    "message_seq": 1,
                }
            ],
        ),
        clock=lambda: next(ticks, 2.0),
        sleeper=no_sleep,
    )
    with pytest.raises(VerificationProbeError, match="verification_canary_failed"):
        asyncio.run(
            adapter.issue_canary(
                target=target,
                canary_id=canary_id,
                database=empty,
                timeout=1.0,
            )
        )


def test_verification_host_lock_refuses_contention(tmp_path: Path) -> None:
    directory = tmp_path / "ledger"
    directory.mkdir()
    lock_path = directory / "setup-verify.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SetupVerificationError, match="verification_lock_unavailable"):
            with setup_verifier._host_lock(directory):
                pass
    finally:
        os.close(descriptor)


def test_verify_malformed_reinspection_and_true_late_process_loss(tmp_path: Path) -> None:
    malformed = {**inspection(waiter="pass", hub_pid=9876), "checks": "bad"}
    receipt = run_verification(
        tmp_path / "generation",
        adapter=RecordingAdapter(),
        inspection_document=malformed,
    )
    assert receipt["failure_code"] == "verification_target_changed"

    probes = iter([True, True, True, True, True, False, False])
    receipt = run_verification(
        tmp_path / "late-process",
        adapter=RecordingAdapter(),
        probe=lambda _pid: next(probes, False),
    )
    assert receipt["failure_code"] == "verification_protected_process_missing"
