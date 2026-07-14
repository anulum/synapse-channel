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
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from e2e.opencode_editors.acp_trace_proxy import TraceWriter
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
                    "authMethods": [{"id": "terminal", "_meta": {"terminal-auth": True}}],
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
        expected_client_names=("editor-client",),
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


def test_trace_writer_refuses_existing_or_symlink_path(tmp_path: Path) -> None:
    existing = tmp_path / "existing.jsonl"
    existing.write_text("owned\n", encoding="utf-8")
    symlink = tmp_path / "link.jsonl"
    symlink.symlink_to(existing)

    with pytest.raises(OSError):
        TraceWriter(existing)
    with pytest.raises(OSError):
        TraceWriter(symlink)


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
