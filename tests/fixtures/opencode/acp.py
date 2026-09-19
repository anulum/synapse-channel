# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real OpenCode ACP acceptance exchange
"""Perform bounded ACP exchanges with a real OpenCode process."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_INITIALIZE_PARAMS = {
    "protocolVersion": 1,
    "clientCapabilities": {"_meta": {"terminal-auth": True}},
    "clientInfo": {"name": "synapse-channel-test", "version": "0.1.0"},
}


def acp_initialize(
    binary: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    """Perform one real ACP initialize exchange and close cleanly on stdin EOF."""
    process = subprocess.Popen(  # nosec B603
        [binary, "acp", "--cwd", str(cwd)],
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise AssertionError("OpenCode ACP pipes were not created")
    stdin = process.stdin
    stdout = process.stdout
    stderr_stream = process.stderr
    responses: queue.Queue[str] = queue.Queue()

    def _read_response() -> None:
        for line in stdout:
            responses.put(line)

    reader = threading.Thread(target=_read_response, daemon=True)
    reader.start()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": _INITIALIZE_PARAMS,
    }
    try:
        stdin.write(json.dumps(request) + "\n")
        stdin.flush()
        line = responses.get(timeout=30)
        stdin.close()
        process.wait(timeout=10)
        stderr = stderr_stream.read()
        if process.returncode != 0:
            raise AssertionError(f"OpenCode ACP exited {process.returncode}: {stderr[-2000:]}")
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise AssertionError("OpenCode ACP returned a non-object JSON-RPC response")
        return decoded, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)


def acp_session_prompt(
    binary: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    prompt: str,
) -> tuple[str, str, str, str]:
    """Exercise ACP session creation and one scripted-provider prompt."""
    process = subprocess.Popen(  # nosec B603
        [binary, "acp", "--cwd", str(cwd)],
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise AssertionError("OpenCode ACP pipes were not created")
    stdin = process.stdin
    stdout = process.stdout
    stderr_stream = process.stderr
    responses: queue.Queue[str] = queue.Queue()
    chunks: list[str] = []

    def _read_response() -> None:
        for line in stdout:
            responses.put(line)

    reader = threading.Thread(target=_read_response, daemon=True)
    reader.start()

    def _exchange(request_id: int, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            + "\n"
        )
        stdin.flush()
        for _ in range(256):
            try:
                message = json.loads(responses.get(timeout=45))
            except queue.Empty as exc:
                raise AssertionError(f"OpenCode ACP {method} timed out") from exc
            if not isinstance(message, dict):
                raise AssertionError("OpenCode ACP returned a non-object JSON-RPC message")
            if message.get("method") == "session/update":
                update = message.get("params", {}).get("update", {})
                content = update.get("content", {})
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
            if message.get("id") == request_id:
                return message
        raise AssertionError(f"OpenCode ACP {method} exceeded the bounded message count")

    try:
        initialize = _exchange(1, "initialize", _INITIALIZE_PARAMS)
        if initialize.get("result", {}).get("protocolVersion") != 1:
            raise AssertionError("OpenCode ACP initialize protocol changed")
        session = _exchange(2, "session/new", {"cwd": str(cwd), "mcpServers": []})
        session_id = session.get("result", {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise AssertionError("OpenCode ACP did not create a session")
        result = _exchange(
            3,
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
        )
        stop_reason = result.get("result", {}).get("stopReason")
        if not isinstance(stop_reason, str):
            raise AssertionError("OpenCode ACP prompt has no stop reason")
        stdin.close()
        process.wait(timeout=10)
        stderr = stderr_stream.read()
        if process.returncode != 0:
            raise AssertionError(f"OpenCode ACP exited {process.returncode}: {stderr[-2000:]}")
        return session_id, stop_reason, "".join(chunks), stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
