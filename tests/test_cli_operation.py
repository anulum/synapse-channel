# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declare an operation and publish its evidence from the command line
"""``synapse operation`` sends one record and reports the hub's verdict by exit code."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_operation as command
from synapse_channel.cli import build_parser
from synapse_channel.core.acl import CLAIM, MESSAGE, OPERATION_EVIDENCE, AclPolicy, AclRule
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification

SHA = "d" * 64


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["operation", *argv])


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_declare_builds_the_exact_effect_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each effect sends only its identifiers; a missing identifier is refused locally."""
    seen: list[dict[str, Any]] = []

    async def fake(*, fields: dict[str, Any], **_kwargs: Any) -> int:
        seen.append(fields)
        return 0

    monkeypatch.setattr(command, "publish_operation_record", fake)
    common = ["--task", "T1", "--name", "P/worker", "--operation-id", "op-1", "--epoch", "2"]
    args = _args("declare", *common, "--effect", "file", "--path", "a", "--sha256", SHA)
    assert command._cmd_declare(args) == 0
    migration = _args(
        "declare",
        *common,
        "--effect",
        "migration",
        "--database",
        "app.db",
        "--migration",
        "0042",
        "--schema-sha256",
        SHA,
        "--budget",
        "0",
    )
    assert command._cmd_declare(migration) == 0
    assert seen[0]["effect_target"] == {"path": "a", "sha256": SHA}
    assert seen[1]["effect_target"] == {
        "database": "app.db",
        "migration": "0042",
        "schema_sha256": SHA,
    }
    assert seen[1]["budget_remaining"] == 0 and "budget_remaining" not in seen[0]
    missing = _args("declare", *common, "--effect", "git_publication", "--branch", "main")
    assert command._cmd_declare(missing) == command.EXIT_UNREADABLE
    for budget in ("-1", "many"):
        with pytest.raises(SystemExit):
            _args("declare", *common, "--effect", "file", "--budget", budget)


def test_declare_takes_the_operation_id_worker_session_gives_its_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --operation-id the declaration names SYN_OPERATION_ID; with neither it is refused."""
    seen: list[dict[str, Any]] = []

    async def fake(*, fields: dict[str, Any], **_kwargs: Any) -> int:
        seen.append(fields)
        return 0

    monkeypatch.setattr(command, "publish_operation_record", fake)
    common = ["--task", "T1", "--name", "P/worker", "--epoch", "2", "--effect", "file"]
    file_target = ["--path", "a", "--sha256", SHA]
    monkeypatch.setenv("SYN_OPERATION_ID", "worker-0a1b")
    assert command._cmd_declare(_args("declare", *common, *file_target)) == 0
    explicit = _args("declare", *common, *file_target, "--operation-id", "op-2")
    assert command._cmd_declare(explicit) == 0
    assert [fields["operation_id"] for fields in seen] == ["worker-0a1b", "op-2"]
    monkeypatch.delenv("SYN_OPERATION_ID")
    assert command._cmd_declare(_args("declare", *common, *file_target)) == command.EXIT_UNREADABLE
    assert "name --operation-id or set SYN_OPERATION_ID" in capsys.readouterr().out
    assert len(seen) == 2


def test_evidence_files_are_read_but_never_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record's own operation id is used unless one is given; bad files exit five."""
    seen: list[dict[str, Any]] = []

    async def fake(*, fields: dict[str, Any], **_kwargs: Any) -> int:
        seen.append(fields)
        return 0

    monkeypatch.setattr(command, "publish_operation_record", fake)
    report = _write(tmp_path / "run.json", {"operation_id": "op-7", "state": "unknown"})
    before = report.read_bytes()
    assert command._cmd_outcome(_args("outcome", str(report), "--task", "T", "--name", "X")) == 0
    verification = _write(tmp_path / "v.json", {"operation_id": "op-7"})
    named = _args(
        "verification", str(verification), "--task", "T", "--name", "X", "--operation-id", "op-8"
    )
    assert command._cmd_verification(named) == 0
    assert [(f["record"], f["operation_id"]) for f in seen] == [
        ("outcome", "op-7"),
        ("verification", "op-8"),
    ]
    assert seen[0]["report"] == {"operation_id": "op-7", "state": "unknown"}
    assert report.read_bytes() == before
    for bad in (
        tmp_path / "absent.json",
        _write(tmp_path / "list.json", []),
        _write(tmp_path / "anon.json", {"state": "unknown"}),
    ):
        assert command._cmd_outcome(_args("outcome", str(bad), "--task", "T", "--name", "X")) == 5
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"operation_id": "op", "pad": "x" * (64 * 1024)}), encoding="utf-8")
    assert command._cmd_outcome(_args("outcome", str(big), "--task", "T", "--name", "X")) == 5
    assert "cannot read" in capsys.readouterr().out


def _policy() -> AclPolicy:
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "", "anyone may claim"),
            AclRule(MESSAGE, "agent", "*", "", "anyone may message"),
            AclRule(OPERATION_EVIDENCE, "claim", "T*", "Q", "checker project"),
        ]
    )


async def test_a_live_hub_accepts_a_declaration_and_its_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The commands drive a real hub end to end and map its verdicts to exit codes."""
    store = EventStore(tmp_path / "hub.db")
    outcome = _write(
        tmp_path / "run.json",
        ExecutionOutcome(
            "op-1", "worker_process", OutcomeState.CONFIRMED_DONE, "provider_process_exited"
        ).to_json(),
    )
    verified = _write(
        tmp_path / "verify.json",
        ResultVerification(
            "op-1", "file_change", "confirmed_done", "observed", "verified", "content_matches"
        ).to_json(),
    )
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_hub, uri):
            worker = await connect_agent("P/worker", uri)
            try:
                await worker.agent.claim("T1", note="cli test")
                grant = await worker.recorder.wait_for(
                    lambda frame: frame.get("type") == MessageType.CLAIM_GRANTED
                )
                options: dict[str, Any] = {
                    "uri": uri,
                    "token": None,
                    "ready_timeout": 3.0,
                    "result_timeout": 3.0,
                }
                declaration = {
                    "record": "declare",
                    "task_id": "T1",
                    "operation_id": "op-1",
                    "epoch": grant["epoch"],
                    "effect": "file",
                    "effect_target": {"path": "out.txt", "sha256": SHA},
                }
                code = await command.publish_operation_record(
                    actor="P/worker-cli", fields=declaration, json_output=True, **options
                )
                assert code == command.EXIT_REFUSED
                assert json.loads(capsys.readouterr().out)["reason"] == "not_claim_holder"
            finally:
                await close_agents(worker)
            # A fresh connection under the worker's own name holds the same claim.
            code = await command.publish_operation_record(
                actor="P/worker", fields=declaration, json_output=False, **options
            )
            assert code == command.EXIT_ACCEPTED
            assert "declare recorded for op-1 at seq" in capsys.readouterr().out
            for path, record, key in (
                (outcome, "outcome", "report"),
                (verified, "verification", "verification"),
            ):
                fields = {
                    "record": record,
                    "task_id": "T1",
                    "operation_id": "op-1",
                    key: json.loads(path.read_text(encoding="utf-8")),
                }
                code = await command.publish_operation_record(
                    actor="Q/checker", fields=fields, json_output=True, **options
                )
                assert code == command.EXIT_ACCEPTED, capsys.readouterr().out
                assert json.loads(capsys.readouterr().out)["status"] == "accepted"
        kinds = (
            EventKind.OPERATION_DECLARED,
            EventKind.OPERATION_OUTCOME,
            EventKind.OPERATION_VERIFIED,
        )
        assert [row.kind for row in store.read_since(0, kinds=kinds)] == list(kinds)
    finally:
        store.close()


class _FakeAgent:
    """A client that answers a request with a fixed frame, or with nothing."""

    def __init__(self, callback: Any, *, reply: dict[str, Any] | None, ready: bool) -> None:
        self.callback = callback
        self.reply = reply
        self.ready = ready
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(1.0)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def request_who(self, hub: str | None = None) -> None:
        """Answer the acceptance check the way an accepting hub does."""
        del hub
        if self.running and self.last_close_code is None:
            await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def send_message(self, _msg_type: str, **fields: Any) -> None:
        if self.reply is not None:
            frame = dict(self.reply)
            if frame.get("type") == MessageType.OPERATION_RECORD_RESULT:
                frame["request_id"] = fields["request_id"]
            await self.callback({"type": "presence_update"})
            await self.callback(frame)


def _factory(reply: dict[str, Any] | None, *, ready: bool) -> Any:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> _FakeAgent:
        return _FakeAgent(callback, reply=reply, ready=ready)

    return factory


async def _run(reply: dict[str, Any] | None, *, ready: bool = True) -> int:
    factory = _factory(reply, ready=ready)

    return await command.publish_operation_record(
        uri="ws://hub",
        actor="P/worker",
        fields={"record": "outcome", "task_id": "T", "operation_id": "op"},
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=False,
        agent_factory=factory,
    )


async def test_hub_errors_silence_and_old_hubs_have_their_own_exit_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An old hub that does not know the verb is told apart from a refusal."""
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_record'."}
    assert await _run(unknown) == command.EXIT_UNSUPPORTED
    assert "unsupported_by_hub" in capsys.readouterr().out
    assert await _run({"type": MessageType.ERROR}) == command.EXIT_REFUSED
    assert "the hub refused the request" in capsys.readouterr().out
    assert await _run({"type": MessageType.OPERATION_RECORD_RESULT}) == command.EXIT_REFUSED
    assert await _run(None) == command.EXIT_NO_VERDICT
    assert "no verdict" in capsys.readouterr().out
    assert await _run(None, ready=False) == command.EXIT_NO_VERDICT
