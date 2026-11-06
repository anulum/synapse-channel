# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provisioning and process commands of the interop lane
"""The interop lane provisions like an operator and hands clients paths, never secrets.

The journeys themselves run in the interop workflow, against the official
clients; these tests cover what the lane prepares for them.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from mcp import ClientSession
from mcp.types import CallToolResult
from tools import interop_a2a, interop_hub, interop_mcp_python, interop_mcp_ts
from tools.interop_hub import (
    RESOURCE,
    InteropProcessError,
    clean_environment,
    free_port,
    http_mcp_command,
    hub_command,
    provision,
    running,
    stdio_mcp_command,
)
from tools.interop_journey import StepFailure, Steps
from tools.interop_oracle import JournalOracle

from synapse_channel.core.identity_binding import parse_identity_trust_document
from synapse_channel.core.persistence import EventStore

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="owner-only modes are POSIX permissions")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@POSIX_ONLY
def test_provisioning_writes_owner_only_operator_material(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    for path in (
        provisioned.hub_token_file,
        provisioned.identity_trust,
        provisioned.grants,
        provisioned.certificate_key,
    ):
        assert _mode(path) == 0o600
    keys = parse_identity_trust_document(json.loads(provisioned.identity_trust.read_text("utf-8")))
    assert sorted(keys) == ["alice-1", "bob-1"]
    grants = json.loads(provisioned.grants.read_text("utf-8"))
    assert grants["resource"] == RESOURCE
    assert grants["subjects"]["bob"]["projects"]["INTEROP"]["seat"] == "INTEROP/bob"
    assert provisioned.hub_token_file.read_text("utf-8").strip() == provisioned.hub_token


def test_access_tokens_are_real_and_counted_as_secrets(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    token = provisioned.access_token("alice")
    other = provisioned.access_token("bob", audience="https://other.interop.test/mcp")
    public = provisioned.issuer_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(token, public, algorithms=["EdDSA"], audience=RESOURCE)
    assert claims["sub"] == "alice" and claims["scope"] == "synapse:read synapse:mutate"
    assert provisioned.secrets() == [provisioned.hub_token, token, other]


def test_commands_carry_paths_and_the_requested_binding(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    plain = hub_command(provisioned, tmp_path / "hub.db", 1234, identity_binding=False)
    bound = hub_command(provisioned, tmp_path / "hub.db", 1234, identity_binding=True)
    assert "--require-identity-binding" not in plain
    assert bound[-3:] == [
        "--identity-trust",
        str(provisioned.identity_trust),
        "--require-identity-binding",
    ]
    commands = [
        plain,
        stdio_mcp_command(provisioned, "ws://127.0.0.1:1", "interop/alpha"),
        http_mcp_command(provisioned, "ws://127.0.0.1:1", 4321),
    ]
    for command in commands:
        assert command[:3] == [sys.executable, "-m", "synapse_channel.cli"]
        assert provisioned.hub_token not in " ".join(command)
        assert "--token-file" in command
    assert "127.0.0.1:4321" in commands[2]


def test_lane_processes_drop_ambient_synapse_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYN_IDENTITY", "user/terminal-1")
    monkeypatch.setenv("SYNAPSE_TOKEN", "ambient")
    monkeypatch.setenv("INTEROP_UNIT_KEEP", "kept")
    environment = clean_environment()
    assert "SYN_IDENTITY" not in environment and "SYNAPSE_TOKEN" not in environment
    assert environment["INTEROP_UNIT_KEEP"] == "kept"
    assert isinstance(free_port(), int)


def test_a_process_that_stops_early_is_named_and_logged(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import sys; print('early stop'); sys.exit(3)"]
    with (
        contextlib.ExitStack() as stack,
        pytest.raises(InteropProcessError, match="unit process exited with code 3"),
    ):
        stack.enter_context(
            running(command, port=free_port(), label="unit process", log=tmp_path / "unit.log")
        )
    assert "early stop" in (tmp_path / "unit.log").read_text("utf-8")


def test_a_listening_process_is_used_and_then_reaped(tmp_path: Path) -> None:
    port = free_port()
    server = (
        "import socket, time\n"
        f"s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(1)\n"
        "time.sleep(60)\n"
    )
    with running(
        [sys.executable, "-c", server], port=port, label="listener", log=tmp_path / "l.log"
    ) as process:
        assert process.poll() is None
    assert process.poll() is not None


def test_the_typescript_client_receives_paths_never_tokens(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    stdio = interop_mcp_ts.configuration(provisioned, "ws://127.0.0.1:1", None, "none")
    servers = stdio["servers"]
    assert stdio["transport"] == "stdio" and isinstance(servers, list) and len(servers) == 2
    http = interop_mcp_ts.configuration(
        provisioned, "ws://127.0.0.1:1", "https://127.0.0.1:2", "surface-disabled"
    )
    text = json.dumps(http)
    for secret in provisioned.secrets():
        assert secret not in text
    token_files = http["token_files"]
    assert isinstance(token_files, list)
    for token_file in token_files:
        token = Path(token_file).read_text("utf-8").strip()
        assert jwt.decode(token, options={"verify_signature": False})["aud"].startswith(
            "https://other"
        )
    version, integrity = interop_mcp_ts.sdk_pin()
    assert version == "1.32.2" and integrity.startswith("sha512-")


@POSIX_ONLY
def test_the_a2a_bridge_is_bearer_protected_and_its_bearer_owner_only(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    bearer = interop_a2a._bearer(provisioned, "unit.bearer", "bearer-value")
    assert _mode(bearer) == 0o600
    command = interop_a2a.bridge_command(provisioned, "ws://127.0.0.1:1", 5555, bearer)
    assert "--bearer-auth" in command and "bearer-value" not in " ".join(command)
    assert command[command.index("--name") + 1] == interop_a2a.BRIDGE
    assert command[command.index("--endpoint-url") + 1] == "http://127.0.0.1:5555"
    other = interop_mcp_python.other_token(provisioned)
    assert _mode(other) == 0o600
    assert other.read_text("utf-8").strip() != provisioned.hub_token


def test_a_conflicting_claim_is_judged_as_each_transport_reports_it() -> None:
    stdio, http = interop_mcp_python.STDIO, interop_mcp_python.HTTP
    refused = f"claim denied: 'X' — Task 'X' is already claimed by {stdio.first}."
    assert interop_mcp_python._refused(stdio, False, refused)
    assert not interop_mcp_python._refused(stdio, False, "claim denied: someone else")
    assert not interop_mcp_python._refused(stdio, True, refused)
    assert interop_mcp_python._refused(http, True, interop_mcp_python.REMOTE_REFUSAL)
    assert not interop_mcp_python._refused(http, False, interop_mcp_python.REMOTE_REFUSAL)


def test_the_mcp_journal_assertions_match_a_real_journey_journal(tmp_path: Path) -> None:
    from synapse_channel.core.journal import (
        EventKind,
        record_chat,
        record_claim_denial,
        record_release,
    )

    who = interop_mcp_python.STDIO
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(EventKind.CLAIM, {"task_id": who.task_id, "owner": who.first}, durable=True)
        record_claim_denial(
            store,
            claimant=who.second,
            task_id=who.task_id,
            reason_code="LEASE_LIVE",
            worktree="",
            paths=[],
        )
        record_chat(
            store,
            {"sender": who.first, "target": who.second, "payload": interop_mcp_python.MARKER},
        )
        record_release(store, who.task_id)
    finally:
        store.close()
    oracle = JournalOracle.read(tmp_path / "hub.db")
    interop_mcp_python.journal_assertions(who)(oracle)
    assert len(oracle.asserted) == 4
    assert interop_hub.STDIO_NAMES == (who.first, who.second)


@POSIX_ONLY
def test_a_process_that_never_listens_is_refused_then_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(interop_hub, "_READY_SECONDS", 0.5)
    monkeypatch.setattr(interop_hub, "_STOP_SECONDS", 0.5)
    stubborn = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready to ignore', flush=True)\n"
        "time.sleep(60)\n"
    )
    with (
        contextlib.ExitStack() as stack,
        pytest.raises(InteropProcessError, match="stubborn did not listen"),
    ):
        stack.enter_context(
            running(
                [sys.executable, "-c", stubborn],
                port=free_port(),
                label="stubborn",
                log=tmp_path / "stubborn.log",
            )
        )


class _Scripted:
    """A client session whose answers a test scripts, to show how each step fails."""

    def __init__(self, **failures: object) -> None:
        self.failures = failures

    async def initialize(self) -> None:
        if self.failures.get("initialize"):
            raise ConnectionResetError("closed")

    async def list_tools(self) -> SimpleNamespace:
        names = (
            ["synapse_board"]
            if self.failures.get("tools")
            else [
                "synapse_claim",
                "synapse_release",
                "synapse_send",
                "synapse_task_declare",
            ]
        )
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in names])

    async def list_resources(self) -> SimpleNamespace:
        uris = [] if self.failures.get("resources") else ["synapse://board"]
        return SimpleNamespace(resources=[SimpleNamespace(uri=uri) for uri in uris])

    async def call_tool(self, tool: str, arguments: object, meta: object = None) -> object:
        if self.failures.get("call") == tool:
            raise TimeoutError("slow")
        text = {
            "synapse_claim": "claim granted: 'X'",
            "synapse_send": "sent to interop/beta",
            "synapse_release": "released 'X'",
        }.get(tool, "")
        return SimpleNamespace(is_error=False, structured_content={"result": text})

    async def read_resource(self, uri: str) -> SimpleNamespace:
        if self.failures.get("board") == "raise":
            raise OSError("gone")
        return SimpleNamespace(contents=[] if self.failures.get("board") == "empty" else ["b"])


@pytest.mark.parametrize(
    ("failures", "step", "outcome"),
    [
        ({"initialize": True}, "initialize-first", "ConnectionResetError"),
        ({"tools": True}, "initialize-first", "tools-missing"),
        ({"resources": True}, "initialize-first", "board-missing"),
        ({"call": "synapse_claim"}, "claim-granted", "TimeoutError"),
        ({}, "claim-conflict-refused", "unexpected-result"),
        ({"board": "raise"}, "board-read", "OSError"),
        ({"board": "empty"}, "board-read", "empty"),
    ],
)
async def test_each_client_step_fails_on_its_own_misbehaviour(
    failures: dict[str, object], step: str, outcome: str
) -> None:
    steps = Steps()
    first = _Scripted(**failures)
    second: _Scripted = _Scripted(
        **{key: value for key, value in failures.items() if key != "board"}
    )
    if step == "board-read":
        second = _ScriptedRefusal()
    with pytest.raises(StepFailure) as failure:
        await interop_mcp_python.journey(
            steps,
            cast(ClientSession, first),
            cast(ClientSession, second),
            interop_mcp_python.STDIO,
            claim_arguments={"task_only": True},
        )
    assert failure.value.step == step
    assert steps.records[-1].outcome == outcome


class _ScriptedRefusal(_Scripted):
    """The second identity, refused the way stdio reports it."""

    async def call_tool(self, tool: str, arguments: object, meta: object = None) -> object:
        holder = interop_mcp_python.STDIO.first
        return SimpleNamespace(
            is_error=False,
            structured_content={"result": f"claim denied: 'X' — already claimed by {holder}."},
        )


def test_a_tool_result_without_structured_text_reads_as_empty() -> None:
    result = CallToolResult(content=[], structured_content=None)
    assert interop_mcp_python._result_text(result) == ""


async def test_a_session_that_cannot_open_fails_its_first_step(tmp_path: Path) -> None:
    provisioned = provision(tmp_path / "p")
    provisioned.certificate.unlink()
    steps = Steps()
    failed = await interop_mcp_python._python_sessions(
        provisioned, "ws://127.0.0.1:1", "https://127.0.0.1:1", "none", steps
    )
    assert failed == "session-open"
    assert steps.records[-1].outcome == "FileNotFoundError"


def test_a_separate_client_that_breaks_or_is_missing_fails_client_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provisioned = provision(tmp_path / "p")
    # The hub interpreter has no a2a-sdk, so the real client script dies before reporting.
    a2a_steps = Steps()
    assert interop_a2a._client_half(
        Path(sys.executable), "http://127.0.0.1:1", provisioned.hub_token_file, a2a_steps
    ) == ("client-run", "", "")
    assert a2a_steps.records[-1].name == "client-run"
    monkeypatch.setattr(interop_mcp_ts, "CLIENT_DIRECTORY", tmp_path)
    monkeypatch.setattr(interop_mcp_ts, "NODE", sys.executable)
    broken = Steps()
    assert (
        interop_mcp_ts.typescript_client_half(provisioned, "ws://127.0.0.1:1", None, "none", broken)
        == "client-run"
    )
    assert broken.records[-1].outcome.startswith("exit-")
    monkeypatch.setattr(interop_mcp_ts, "NODE", str(tmp_path / "no-such-node"))
    missing = Steps()
    assert (
        interop_mcp_ts.typescript_client_half(
            provisioned, "ws://127.0.0.1:1", None, "none", missing
        )
        == "client-run"
    )
    assert missing.records[-1].outcome == "node-missing"
