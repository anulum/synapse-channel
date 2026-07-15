# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode editor ACP trace validation regressions
"""Exercise corrupt and incomplete editor evidence through the public contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeAlias

import pytest

from e2e.opencode_editors.trace_contract import assert_editor_trace, prompt_sha256
from fixtures.opencode.process import OPENCODE_VERSION

_MAX_TRACE_BYTES = 4_194_304
_PROMPT = "Use secret-token-value only as an opaque acceptance prompt."
_CLIENTS = {"editor-client": "1.0"}

TraceEvent: TypeAlias = dict[str, object]


def _valid_events() -> list[TraceEvent]:
    return [
        {
            "direction": "client_to_agent",
            "id": 1,
            "method": "initialize",
            "protocol_version": 1,
            "client_info": {"name": "editor-client", "version": "1.0"},
        },
        {
            "direction": "agent_to_client",
            "id": 1,
            "response_to": "initialize",
            "error": False,
            "protocol_version": 1,
            "agent_info": {"name": "OpenCode", "version": OPENCODE_VERSION},
            "mcp_capabilities": {"http": True, "sse": True},
            "terminal_auth_method": True,
        },
        {
            "direction": "client_to_agent",
            "id": 2,
            "method": "session/new",
        },
        {
            "direction": "agent_to_client",
            "id": 2,
            "response_to": "session/new",
            "error": False,
            "session_id_present": True,
        },
        {
            "direction": "client_to_agent",
            "id": 3,
            "method": "session/prompt",
            "prompt_bytes": len(_PROMPT.encode("utf-8")),
            "prompt_sha256": prompt_sha256(_PROMPT),
        },
        {
            "direction": "agent_to_client",
            "id": 3,
            "response_to": "session/prompt",
            "error": False,
            "stop_reason": "end_turn",
        },
    ]


def _write_events(path: Path, events: list[TraceEvent]) -> None:
    payload = "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)


def _assert_trace(path: Path, *, expected_clients: dict[str, str] | None = None) -> None:
    assert_editor_trace(
        path,
        expected_clients=_CLIENTS if expected_clients is None else expected_clients,
        expected_agent_version=OPENCODE_VERSION,
        prompt=_PROMPT,
    )


def test_trace_contract_accepts_the_emitted_evidence_schema(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_events(trace, _valid_events())

    _assert_trace(trace)


def test_trace_contract_accepts_json_rpc_notifications(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events.insert(4, {"direction": "agent_to_client", "method": "session/update"})
    _write_events(trace, events)

    _assert_trace(trace)


def test_trace_contract_accepts_a_completed_agent_request(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events[4:4] = [
        {
            "direction": "agent_to_client",
            "id": 8,
            "method": "session/request_permission",
        },
        {
            "direction": "client_to_agent",
            "id": 8,
            "response_to": "session/request_permission",
            "error": False,
        },
    ]
    _write_events(trace, events)

    _assert_trace(trace)


def test_trace_contract_uses_portable_open_when_nofollow_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_events(trace, _valid_events())
    monkeypatch.delattr(os, "O_NOFOLLOW")

    _assert_trace(trace)


def test_trace_contract_refuses_a_non_regular_trace(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.mkdir(mode=0o700)

    with pytest.raises(AssertionError, match="regular ACP trace"):
        _assert_trace(trace)


def test_trace_contract_refuses_a_non_private_trace(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_events(trace, _valid_events())
    trace.chmod(0o640)

    with pytest.raises(AssertionError, match="private owned file"):
        _assert_trace(trace)


def test_trace_contract_refuses_a_trace_larger_than_the_bound(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    with trace.open("wb") as stream:
        stream.truncate(_MAX_TRACE_BYTES + 1)
    trace.chmod(0o600)

    with pytest.raises(AssertionError, match="exceeds four MiB"):
        _assert_trace(trace)


def test_trace_contract_refuses_growth_after_the_metadata_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(b"x" * (_MAX_TRACE_BYTES + 1))
    trace.chmod(0o600)
    real_fstat = os.fstat

    def hide_size(descriptor: int) -> os.stat_result:
        fields = list(real_fstat(descriptor))
        fields[6] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", hide_size)

    with pytest.raises(AssertionError, match="exceeds four MiB"):
        _assert_trace(trace)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff\n", "not valid UTF-8"),
        (b"not-json\n", "invalid ACP trace JSON"),
        (b"[]\n", "non-object ACP trace event"),
        (b"", "ACP trace is empty"),
    ],
)
def test_trace_contract_refuses_malformed_trace_files(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(payload)
    trace.chmod(0o600)

    with pytest.raises(AssertionError, match=message):
        _assert_trace(trace)


def test_trace_contract_refuses_a_missing_bundle(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="did not produce an ACP trace bundle"):
        _assert_trace(tmp_path / "missing.jsonl")


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"direction": "sideways", "method": "session/update"}, "invalid traffic direction"),
        (
            {"direction": "client_to_agent", "id": True, "method": "session/update"},
            "invalid request id",
        ),
        (
            {"direction": "client_to_agent", "id": 9},
            "uncorrelated protocol event",
        ),
        (
            {
                "direction": "agent_to_client",
                "id": 9,
                "response_to": "session/update",
                "error": False,
            },
            "unknown or out-of-order response id",
        ),
    ],
)
def test_trace_contract_refuses_uncorrelated_events(
    tmp_path: Path, event: TraceEvent, message: str
) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events.insert(0, event)
    _write_events(trace, events)

    with pytest.raises(AssertionError, match=message):
        _assert_trace(trace)


def test_trace_contract_refuses_a_reused_pending_id(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events.insert(1, {"direction": "client_to_agent", "id": 1, "method": "session/new"})
    _write_events(trace, events)

    with pytest.raises(AssertionError, match="reuses a pending request id"):
        _assert_trace(trace)


def test_trace_contract_refuses_an_unanswered_additional_request(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events.append(
        {
            "direction": "client_to_agent",
            "id": 4,
            "method": "session/set_config_option",
        }
    )
    _write_events(trace, events)

    with pytest.raises(AssertionError, match="requests without responses"):
        _assert_trace(trace)


def test_trace_contract_refuses_a_mismatched_response_method(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events[1]["response_to"] = "session/new"
    _write_events(trace, events)

    with pytest.raises(AssertionError, match="response method does not match"):
        _assert_trace(trace)


@pytest.mark.parametrize(
    ("index", "field", "value", "message"),
    [
        (0, "protocol_version", 2, "request ACP protocol version 1"),
        (1, "protocol_version", 2, "negotiate ACP protocol version 1"),
        (1, "agent_info", {"name": "other", "version": OPENCODE_VERSION}, "OpenCode"),
        (3, "session_id_present", False, "return a session id"),
        (4, "prompt_bytes", 1, "prompt length"),
        (4, "prompt_sha256", "0" * 64, "prompt digest"),
        (5, "stop_reason", "cancelled", "did not end cleanly"),
    ],
)
def test_trace_contract_refuses_tampered_lifecycle_metadata(
    tmp_path: Path, index: int, field: str, value: object, message: str
) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    events[index][field] = value
    _write_events(trace, events)

    with pytest.raises(AssertionError, match=message):
        _assert_trace(trace)


@pytest.mark.parametrize(
    "client_info",
    [None, {"name": 7, "version": "1.0"}, {"name": "editor-client", "version": "0.9"}],
)
def test_trace_contract_refuses_missing_or_invalid_client_metadata(
    tmp_path: Path, client_info: object
) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    if client_info is None:
        events[0].pop("client_info")
    else:
        events[0]["client_info"] = client_info
    _write_events(trace, events)

    with pytest.raises(
        AssertionError,
        match="implementation metadata|unexpected ACP client|unexpected version",
    ):
        _assert_trace(trace)


def test_trace_contract_requires_an_exact_client_allowlist(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_events(trace, _valid_events())

    with pytest.raises(ValueError, match="exact ACP client identity"):
        _assert_trace(trace, expected_clients={})


def test_trace_contract_refuses_lifecycle_pairs_out_of_order(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    events = _valid_events()
    reordered = [events[2], events[3], events[0], events[1], events[4], events[5]]
    _write_events(trace, reordered)

    with pytest.raises(AssertionError, match="lifecycle events arrived out of order"):
        _assert_trace(trace)
