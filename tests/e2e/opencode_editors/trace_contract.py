# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed editor ACP trace contract
"""Validate that a real editor completed an OpenCode ACP prompt turn."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_MAX_TRACE_BYTES = 4_194_304
_MAX_TRACE_SEGMENTS = 8
_HANDSHAKE_METHODS = ("initialize", "session/new")


def prompt_sha256(prompt: str) -> str:
    """Return the canonical prompt fingerprint used by the evidence proxy."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _read_trace(path: Path) -> list[dict[str, Any]]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AssertionError("editor ACP trace could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AssertionError("editor did not produce a regular ACP trace")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise AssertionError("editor ACP trace is not a private owned file")
        if metadata.st_size > _MAX_TRACE_BYTES:
            raise AssertionError("editor ACP trace exceeds four MiB")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(_MAX_TRACE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > _MAX_TRACE_BYTES:
        raise AssertionError("editor ACP trace exceeds four MiB")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AssertionError("editor ACP trace is not valid UTF-8") from exc
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"invalid ACP trace JSON on line {line_number}") from exc
        if not isinstance(event, dict):
            raise AssertionError(f"non-object ACP trace event on line {line_number}")
        events.append(event)
    if not events:
        raise AssertionError("editor ACP trace is empty")
    return events


def _read_trace_bundle(path: Path) -> list[list[dict[str, Any]]]:
    paths = (path, *(Path(f"{path}.{index}") for index in range(1, _MAX_TRACE_SEGMENTS)))
    traces: list[list[dict[str, Any]]] = []
    missing_segment = False
    for candidate in paths:
        try:
            trace = _read_trace(candidate)
        except FileNotFoundError:
            missing_segment = True
            continue
        if missing_segment:
            raise AssertionError("editor ACP trace bundle is not contiguous")
        traces.append(trace)
    overflow = Path(f"{path}.{_MAX_TRACE_SEGMENTS}")
    try:
        _read_trace(overflow)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError(f"editor ACP trace bundle exceeds {_MAX_TRACE_SEGMENTS} segments")
    if not traces:
        raise AssertionError("editor did not produce an ACP trace bundle")
    return traces


def _request(events: Iterable[Mapping[str, Any]], method: str) -> Mapping[str, Any]:
    matches = [
        event
        for event in events
        if event.get("direction") == "client_to_agent" and event.get("method") == method
    ]
    if len(matches) != 1:
        raise AssertionError(f"real editor sent ACP {method} {len(matches)} times instead of once")
    return matches[0]


def _response(events: Iterable[Mapping[str, Any]], method: str) -> Mapping[str, Any]:
    matches = [
        event
        for event in events
        if event.get("direction") == "agent_to_client" and event.get("response_to") == method
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"real editor received {len(matches)} ACP {method} responses instead of one"
        )
    if matches[0].get("error") is not False:
        raise AssertionError(f"OpenCode returned an ACP error for {method}")
    return matches[0]


def assert_editor_trace(
    path: Path,
    *,
    expected_client_names: Iterable[str],
    expected_agent_version: str,
    prompt: str,
) -> None:
    """Assert one prompt round trip across bounded exclusive lifecycle traces."""
    expected_names = set(expected_client_names)
    traces = _read_trace_bundle(path)
    prompt_traces: list[list[dict[str, Any]]] = []
    for events in traces:
        requests = {method: _request(events, method) for method in _HANDSHAKE_METHODS}
        responses = {method: _response(events, method) for method in _HANDSHAKE_METHODS}

        initialize = requests["initialize"]
        if initialize.get("protocol_version") != 1:
            raise AssertionError("editor did not request ACP protocol version 1")
        client_info = initialize.get("client_info")
        client_name = client_info.get("name") if isinstance(client_info, Mapping) else None
        if client_name not in expected_names:
            raise AssertionError(f"unexpected ACP client identity: {client_name!r}")

        initialize_response = responses["initialize"]
        if initialize_response.get("protocol_version") != 1:
            raise AssertionError("OpenCode did not negotiate ACP protocol version 1")
        agent_info = initialize_response.get("agent_info")
        if not isinstance(agent_info, Mapping) or agent_info.get("name") != "OpenCode":
            raise AssertionError("ACP peer did not identify itself as OpenCode")
        if agent_info.get("version") != expected_agent_version:
            raise AssertionError("ACP peer reported an unexpected OpenCode version")
        if initialize_response.get("mcp_capabilities") != {"http": True, "sse": True}:
            raise AssertionError("OpenCode did not advertise the required MCP capabilities")
        if initialize_response.get("terminal_auth_method") is not True:
            raise AssertionError("OpenCode did not advertise terminal authentication")
        if responses["session/new"].get("session_id_present") is not True:
            raise AssertionError("OpenCode did not return a session id")
        if any(
            event.get("direction") == "client_to_agent" and event.get("method") == "session/prompt"
            for event in events
        ):
            prompt_traces.append(events)

    if len(prompt_traces) != 1:
        raise AssertionError(
            f"real editor used {len(prompt_traces)} prompt lifecycle traces instead of one"
        )
    events = prompt_traces[0]
    prompt_request = _request(events, "session/prompt")
    prompt_response = _response(events, "session/prompt")

    if prompt_request.get("prompt_bytes") != len(prompt.encode("utf-8")):
        raise AssertionError("editor prompt length differs from the acceptance prompt")
    if prompt_request.get("prompt_sha256") != prompt_sha256(prompt):
        raise AssertionError("editor prompt digest differs from the acceptance prompt")
    if prompt_response.get("stop_reason") != "end_turn":
        raise AssertionError("OpenCode editor turn did not end cleanly")

    requests = {method: _request(events, method) for method in _HANDSHAKE_METHODS}
    responses = {method: _response(events, method) for method in _HANDSHAKE_METHODS}
    positions = {id(event): index for index, event in enumerate(events)}
    ordered = (
        requests["initialize"],
        responses["initialize"],
        requests["session/new"],
        responses["session/new"],
        prompt_request,
        prompt_response,
    )
    if [positions[id(event)] for event in ordered] != sorted(
        positions[id(event)] for event in ordered
    ):
        raise AssertionError("ACP editor lifecycle events arrived out of order")
