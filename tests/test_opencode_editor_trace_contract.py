# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode editor ACP evidence contract
"""Verify bounded, content-minimised ACP evidence and lifecycle assertions."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from e2e.opencode_editors.acp_trace_proxy import (
    TraceWriter,
    _forward_client_line,
    _lifecycle_environment,
    _new_trace_writer,
)
from e2e.opencode_editors.trace_contract import assert_editor_trace
from fixtures.opencode.process import OPENCODE_VERSION

_PROMPT = "Use secret-token-value only as an opaque acceptance prompt."


def _messages() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "client_to_agent",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "editor-client", "version": "1.0"},
                },
            },
        ),
        (
            "agent_to_client",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "OpenCode", "version": OPENCODE_VERSION},
                    "agentCapabilities": {"mcpCapabilities": {"http": True, "sse": True}},
                    "authMethods": [
                        {
                            "id": "opencode-login",
                            "name": "Login with opencode",
                            "description": "Run `opencode auth login` in the terminal",
                            "_meta": {
                                "terminal-auth": {
                                    "command": "opencode",
                                    "args": ["auth", "login"],
                                    "label": "OpenCode Login",
                                }
                            },
                        }
                    ],
                },
            },
        ),
        (
            "client_to_agent",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"cwd": "/tmp/project", "mcpServers": []},
            },
        ),
        (
            "agent_to_client",
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "session-1"}},
        ),
        (
            "client_to_agent",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {"prompt": [{"type": "text", "text": _PROMPT}]},
            },
        ),
        (
            "agent_to_client",
            {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}},
        ),
    ]


def _write_trace(
    path: Path,
    *,
    mutate: Callable[[list[tuple[str, dict[str, Any]]]], None] | None = None,
) -> None:
    messages = _messages()
    if mutate is not None:
        mutate(messages)
    writer = TraceWriter(path)
    try:
        for direction, message in messages:
            writer.record(direction, json.dumps(message).encode("utf-8"))
    finally:
        writer.close()


def _assert_trace(path: Path) -> None:
    assert_editor_trace(
        path,
        expected_clients={"editor-client": "1.0"},
        expected_agent_version=OPENCODE_VERSION,
        prompt=_PROMPT,
    )


def test_trace_contract_accepts_minimised_real_lifecycle(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"

    _write_trace(trace)
    _assert_trace(trace)

    raw = trace.read_text(encoding="utf-8")
    assert _PROMPT not in raw
    assert "secret-token-value" not in raw
    assert stat.S_IMODE(trace.stat().st_mode) == 0o600
    assert '"mcp_capabilities":{"http":true,"sse":true}' in raw
    assert '"terminal_auth_capable":false' in raw
    assert '"terminal_auth_injected":false' in raw


@pytest.mark.parametrize(
    "client_capabilities",
    [
        {"auth": {"terminal": True}},
        {"_meta": {"terminal-auth": True}},
    ],
)
def test_trace_writer_normalises_terminal_auth_capability(
    tmp_path: Path,
    client_capabilities: dict[str, Any],
) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages[0][1]["params"]["clientCapabilities"] = client_capabilities

    _write_trace(trace, mutate=mutate)
    initialize = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])

    assert initialize["terminal_auth_capable"] is True


@pytest.mark.parametrize(
    ("client_capabilities", "injected"),
    [
        ({}, True),
        ({"auth": {"terminal": True}}, True),
        ({"_meta": {"terminal-auth": True}}, False),
    ],
)
def test_proxy_forwards_legacy_terminal_auth_opt_in(
    client_capabilities: dict[str, Any], injected: bool
) -> None:
    request = _messages()[0][1]
    request["params"]["clientCapabilities"] = client_capabilities
    raw = json.dumps(request).encode("utf-8") + b"\n"

    forwarded, actual_injected = _forward_client_line(raw)

    message = json.loads(forwarded)
    assert actual_injected is injected
    assert message["params"]["clientCapabilities"]["_meta"]["terminal-auth"] is True
    if "auth" in client_capabilities:
        assert message["params"]["clientCapabilities"]["auth"] == {"terminal": True}


@pytest.mark.parametrize(
    "client_capabilities",
    [
        {"auth": {"terminal": False}},
        {"_meta": {"terminal-auth": False}},
        {"auth": {"terminal": False}, "_meta": {"terminal-auth": False}},
    ],
)
def test_proxy_never_overrides_explicit_terminal_auth_refusal(
    client_capabilities: dict[str, Any],
) -> None:
    request = _messages()[0][1]
    request["params"]["clientCapabilities"] = client_capabilities
    raw = json.dumps(request).encode("utf-8") + b"\n"

    forwarded, injected = _forward_client_line(raw)

    assert forwarded == raw
    assert injected is False


@pytest.mark.parametrize(
    "client_capabilities",
    [
        {"auth": {"terminal": False}, "_meta": {"terminal-auth": True}},
        {"auth": {"terminal": "yes"}},
        {"_meta": {"terminal-auth": {"command": "opencode"}}},
    ],
)
def test_proxy_refuses_conflicting_or_non_boolean_client_capabilities(
    client_capabilities: dict[str, Any],
) -> None:
    request = _messages()[0][1]
    request["params"]["clientCapabilities"] = client_capabilities

    with pytest.raises(ValueError):
        _forward_client_line(json.dumps(request).encode("utf-8") + b"\n")


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"jsonrpc":"1.0","method":"initialize"}',
        b'{"jsonrpc":"2.0","id":true,"result":{}}',
    ],
)
def test_trace_writer_refuses_malformed_frames(tmp_path: Path, payload: bytes) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    try:
        with pytest.raises(ValueError):
            writer.record("client_to_agent", payload)
    finally:
        writer.close()


def test_trace_writer_refuses_oversized_frame(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    try:
        with pytest.raises(ValueError, match="one MiB"):
            writer.record("client_to_agent", b"x" * 1_048_577)
    finally:
        writer.close()


def test_proxy_process_failure_wins_clean_child_exit_race(tmp_path: Path) -> None:
    proxy = Path(__file__).resolve().parent / "e2e" / "opencode_editors" / "acp_trace_proxy.py"
    clean_child = shutil.which("true")
    assert clean_child is not None

    for index in range(20):
        completed = subprocess.run(  # nosec B603
            [
                sys.executable,
                str(proxy),
                "--trace",
                str(tmp_path / f"trace-{index}.jsonl"),
                "--opencode-bin",
                clean_child,
                "--cwd",
                str(tmp_path),
            ],
            input=b"\xff\n",
            capture_output=True,
            check=False,
            timeout=10,
        )

        assert completed.returncode == 70
        assert b"ACP trace proxy refused client_to_agent stream" in completed.stderr


def test_trace_writer_refuses_unknown_or_reused_request_ids(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    try:
        writer.record("client_to_agent", json.dumps(request).encode())
        with pytest.raises(ValueError, match="reused"):
            writer.record("client_to_agent", json.dumps(request).encode())
        with pytest.raises(ValueError, match="no pending"):
            writer.record(
                "agent_to_client",
                b'{"jsonrpc":"2.0","id":999,"result":{}}',
            )
    finally:
        writer.close()


def test_trace_writer_correlates_agent_requests(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "trace.jsonl")
    try:
        writer.record(
            "agent_to_client",
            b'{"jsonrpc":"2.0","id":8,"method":"session/request_permission","params":{}}',
        )
        writer.record(
            "client_to_agent",
            b'{"jsonrpc":"2.0","id":8,"result":{"outcome":"cancelled"}}',
        )
    finally:
        writer.close()


@pytest.mark.parametrize(
    "response",
    [
        {"jsonrpc": "2.0", "id": 8},
        {
            "jsonrpc": "2.0",
            "id": 8,
            "result": {},
            "error": {"code": -32603, "message": "conflict"},
        },
        {"jsonrpc": "2.0", "id": 8, "error": None},
        {"jsonrpc": "2.0", "id": 8, "error": {"message": "missing code"}},
        {"jsonrpc": "2.0", "id": 8, "error": {"code": True, "message": "bad"}},
        {"jsonrpc": "2.0", "id": 8, "error": {"code": -32603, "message": 7}},
        {"jsonrpc": "2.0", "id": 8, "error": {"code": -32603, "message": ""}},
    ],
)
def test_malformed_response_is_rejected_without_losing_correlation(
    tmp_path: Path,
    response: dict[str, Any],
) -> None:
    """Reject malformed responses while preserving the pending request."""
    trace = tmp_path / "trace.jsonl"
    messages = _messages()
    writer = TraceWriter(trace)
    try:
        for direction, message in messages[:4]:
            writer.record(direction, json.dumps(message).encode("utf-8"))
        writer.record(
            "agent_to_client",
            b'{"jsonrpc":"2.0","id":8,"method":"session/request_permission"}',
        )
        with pytest.raises(ValueError):
            writer.record(
                "client_to_agent",
                json.dumps(response).encode("utf-8"),
            )
        writer.record(
            "client_to_agent",
            b'{"jsonrpc":"2.0","id":8,"result":{"outcome":"cancelled"}}',
        )
        for direction, message in messages[4:]:
            writer.record(direction, json.dumps(message).encode("utf-8"))
    finally:
        writer.close()

    _assert_trace(trace)


def test_trace_writer_refuses_existing_or_symlink_path(tmp_path: Path) -> None:
    existing = tmp_path / "existing.jsonl"
    existing.write_text("owned\n", encoding="utf-8")
    symlink = tmp_path / "link.jsonl"
    symlink.symlink_to(existing)

    with pytest.raises(OSError):
        TraceWriter(existing)
    with pytest.raises(OSError):
        TraceWriter(symlink)


def test_proxy_allocates_private_exclusive_lifecycle_segments(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    first = _new_trace_writer(trace)
    second = _new_trace_writer(trace)
    first.close()
    second.close()

    assert trace.is_file()
    assert Path(f"{trace}.1").is_file()
    assert stat.S_IMODE(trace.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{trace}.1").stat().st_mode) == 0o600


def test_proxy_refuses_unsafe_existing_lifecycle_segment(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text("occupied\n", encoding="utf-8")
    trace.chmod(0o644)

    with pytest.raises(OSError, match="not a private owned file"):
        _new_trace_writer(trace)


def test_proxy_refuses_more_than_bounded_lifecycle_segments(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    writers = [_new_trace_writer(trace) for _ in range(8)]
    try:
        with pytest.raises(RuntimeError, match="exceeded 8 lifecycle segments"):
            _new_trace_writer(trace)
    finally:
        for writer in writers:
            writer.close()


def test_proxy_isolates_each_lifecycle_runtime(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    first = _new_trace_writer(trace)
    second = _new_trace_writer(trace)
    inherited = {"HOME": "/shared", "OPENCODE_CONFIG_CONTENT": '{"model":"test"}'}
    try:
        first_environment = _lifecycle_environment(first.path, inherited)
        second_environment = _lifecycle_environment(second.path, inherited)
    finally:
        first.close()
        second.close()

    assert first_environment["OPENCODE_CONFIG_CONTENT"] == '{"model":"test"}'
    assert second_environment["OPENCODE_CONFIG_CONTENT"] == '{"model":"test"}'
    isolated_keys = (
        "HOME",
        "OPENCODE_TEST_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    )
    for key in isolated_keys:
        first_path = Path(first_environment[key])
        second_path = Path(second_environment[key])
        assert first_path != second_path
        assert stat.S_IMODE(first_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(second_path.stat().st_mode) == 0o700


def test_trace_contract_accepts_one_prompt_across_lifecycle_segments(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    messages = _messages()
    writer = TraceWriter(trace)
    try:
        for direction, message in messages[:4]:
            writer.record(direction, json.dumps(message).encode("utf-8"))
    finally:
        writer.close()
    _write_trace(Path(f"{trace}.1"))

    _assert_trace(trace)


def test_trace_contract_refuses_unsafe_lifecycle_segment(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    unsafe = Path(f"{trace}.1")
    unsafe.symlink_to(trace)

    with pytest.raises(AssertionError, match="opened safely"):
        _assert_trace(trace)


def test_trace_contract_refuses_noncontiguous_lifecycle_segments(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    _write_trace(Path(f"{trace}.2"))

    with pytest.raises(AssertionError, match="not contiguous"):
        _assert_trace(trace)


def test_trace_contract_refuses_overflow_lifecycle_segment(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    for index in range(1, 9):
        _write_trace(Path(f"{trace}.{index}"))

    with pytest.raises(AssertionError, match="exceeds 8 segments"):
        _assert_trace(trace)


def test_trace_contract_refuses_prompts_across_multiple_lifecycles(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    _write_trace(Path(f"{trace}.1"))

    with pytest.raises(AssertionError, match="2 prompt lifecycle traces"):
        _assert_trace(trace)


def test_trace_contract_refuses_wrong_client(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages[0][1]["params"]["clientInfo"]["name"] = "unexpected"

    _write_trace(trace, mutate=mutate)
    with pytest.raises(AssertionError, match="unexpected ACP client"):
        _assert_trace(trace)


def test_trace_contract_refuses_wrong_agent_version(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages[1][1]["result"]["agentInfo"]["version"] = "0.0.0"

    _write_trace(trace, mutate=mutate)
    with pytest.raises(AssertionError, match="unexpected OpenCode version"):
        _assert_trace(trace)


def test_trace_contract_refuses_missing_mcp_capabilities(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        result = messages[1][1]["result"]
        result["agentCapabilities"] = {"mcpCapabilities": {"http": True, "sse": False}}

    _write_trace(trace, mutate=mutate)
    with pytest.raises(AssertionError, match="MCP capabilities"):
        _assert_trace(trace)


def test_trace_contract_refuses_missing_opencode_terminal_auth(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages[1][1]["result"]["authMethods"] = []

    _write_trace(trace, mutate=mutate)
    with pytest.raises(AssertionError, match="terminal authentication"):
        _assert_trace(trace)


@pytest.mark.parametrize(
    "terminal_auth",
    [
        True,
        {"command": "shell", "args": ["auth", "login"], "label": "OpenCode Login"},
        {"command": "opencode", "args": ["login"], "label": "OpenCode Login"},
        {"command": "opencode", "args": ["auth", "login"], "label": ""},
    ],
)
def test_trace_contract_refuses_non_command_terminal_auth_metadata(
    tmp_path: Path, terminal_auth: object
) -> None:
    trace = tmp_path / "trace.jsonl"

    def mutate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        methods = messages[1][1]["result"]["authMethods"]
        methods[0]["_meta"]["terminal-auth"] = terminal_auth

    _write_trace(trace, mutate=mutate)
    with pytest.raises(AssertionError, match="terminal authentication"):
        _assert_trace(trace)


def test_trace_contract_refuses_error_or_duplicate_prompt(tmp_path: Path) -> None:
    error_trace = tmp_path / "error.jsonl"

    def make_error(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages[-1] = (
            "agent_to_client",
            {"jsonrpc": "2.0", "id": 3, "error": {"code": -32603, "message": "failed"}},
        )

    _write_trace(error_trace, mutate=make_error)
    with pytest.raises(AssertionError, match="ACP error"):
        _assert_trace(error_trace)

    duplicate_trace = tmp_path / "duplicate.jsonl"

    def add_duplicate(messages: list[tuple[str, dict[str, Any]]]) -> None:
        messages.extend(
            [
                (
                    "client_to_agent",
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "session/prompt",
                        "params": {"prompt": [{"type": "text", "text": _PROMPT}]},
                    },
                ),
                (
                    "agent_to_client",
                    {"jsonrpc": "2.0", "id": 4, "result": {"stopReason": "end_turn"}},
                ),
            ]
        )

    _write_trace(duplicate_trace, mutate=add_duplicate)
    with pytest.raises(AssertionError, match="2 times"):
        _assert_trace(duplicate_trace)
