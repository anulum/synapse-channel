# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded ACP traffic evidence proxy for editor E2E
"""Relay an editor's ACP stream to OpenCode while recording safe evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

_MAX_LINE_BYTES = 1_048_576
_MAX_TRACE_BYTES = 4_194_304
_MAX_PENDING_REQUESTS = 4_096
_DIRECTIONS = frozenset({"client_to_agent", "agent_to_client"})
_MISSING = object()


def _request_id(value: object) -> int | str | None:
    """Return a valid JSON-RPC request id without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return value


def _opposite(direction: str) -> str:
    """Return the request direction corresponding to a response direction."""
    return "agent_to_client" if direction == "client_to_agent" else "client_to_agent"


def _prompt_fingerprint(params: object) -> tuple[int, str]:
    """Return the text byte count and digest without retaining prompt content."""
    if not isinstance(params, Mapping):
        return 0, hashlib.sha256(b"").hexdigest()
    prompt = params.get("prompt")
    if not isinstance(prompt, list):
        return 0, hashlib.sha256(b"").hexdigest()
    text = "".join(
        str(block.get("text"))
        for block in prompt
        if isinstance(block, Mapping) and isinstance(block.get("text"), str)
    )
    encoded = text.encode("utf-8")
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _forward_client_line(raw_line: bytes) -> tuple[bytes, bool]:
    """Add OpenCode's legacy terminal-auth opt-in without hiding client intent."""
    try:
        message = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw_line, False
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or message.get("method") != "initialize"
    ):
        return raw_line, False
    params = message.get("params")
    if not isinstance(params, dict):
        return raw_line, False
    raw_capabilities = params.get("clientCapabilities", _MISSING)
    if raw_capabilities is _MISSING:
        capabilities: dict[str, Any] = {}
    elif isinstance(raw_capabilities, dict):
        capabilities = raw_capabilities
    else:
        raise ValueError("ACP initialize clientCapabilities is not an object")

    raw_auth = capabilities.get("auth", _MISSING)
    if raw_auth is _MISSING:
        auth: Mapping[str, Any] = {}
    elif isinstance(raw_auth, Mapping):
        auth = raw_auth
    else:
        raise ValueError("ACP initialize auth capability is not an object")
    raw_meta = capabilities.get("_meta", _MISSING)
    if raw_meta is _MISSING:
        meta: Mapping[str, Any] = {}
    elif isinstance(raw_meta, Mapping):
        meta = raw_meta
    else:
        raise ValueError("ACP initialize capability metadata is not an object")

    standard = auth.get("terminal", _MISSING)
    legacy = meta.get("terminal-auth", _MISSING)
    for name, value in (("auth.terminal", standard), ("_meta.terminal-auth", legacy)):
        if value is not _MISSING and not isinstance(value, bool):
            raise ValueError(f"ACP initialize {name} capability is not boolean")
    if {standard, legacy} == {True, False}:
        raise ValueError("ACP initialize terminal-auth capabilities conflict")
    if standard is False or legacy is False or legacy is True:
        return raw_line, False

    forwarded_meta = dict(meta)
    forwarded_meta["terminal-auth"] = True
    forwarded_capabilities = dict(capabilities)
    forwarded_capabilities["_meta"] = forwarded_meta
    forwarded_params = dict(params)
    forwarded_params["clientCapabilities"] = forwarded_capabilities
    forwarded_message = dict(message)
    forwarded_message["params"] = forwarded_params
    forwarded = json.dumps(forwarded_message, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(forwarded) > _MAX_LINE_BYTES:
        raise ValueError("normalised ACP initialize exceeds the one MiB limit")
    return forwarded, True


def _has_terminal_auth_method(methods: object) -> bool:
    """Recognise the exact command-shaped terminal-auth method from OpenCode."""
    if not isinstance(methods, list):
        return False
    for item in methods:
        if not isinstance(item, Mapping) or item.get("id") != "opencode-login":
            continue
        meta = item.get("_meta")
        if not isinstance(meta, Mapping):
            continue
        terminal_auth = meta.get("terminal-auth")
        if not isinstance(terminal_auth, Mapping):
            continue
        label = terminal_auth.get("label")
        if (
            terminal_auth.get("command") == "opencode"
            and terminal_auth.get("args") == ["auth", "login"]
            and isinstance(label, str)
            and bool(label.strip())
        ):
            return True
    return False


class TraceWriter:
    """Write one private, append-only, content-minimised JSONL trace."""

    def __init__(self, path: Path) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8")
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, int | str], str] = {}
        self._written_bytes = 0

    def close(self) -> None:
        """Flush and close the trace."""
        with self._lock:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()

    def record(
        self,
        direction: str,
        raw_line: bytes,
        *,
        terminal_auth_injected: bool = False,
    ) -> None:
        """Record protocol metadata from one bounded JSON-RPC line."""
        if len(raw_line) > _MAX_LINE_BYTES:
            raise ValueError("ACP line exceeds the one MiB evidence limit")
        try:
            message = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("ACP evidence contains invalid UTF-8 JSON") from exc
        if not isinstance(message, dict):
            raise ValueError("ACP evidence message is not an object")
        if message.get("jsonrpc") != "2.0":
            raise ValueError("ACP evidence message is not JSON-RPC 2.0")
        if direction not in _DIRECTIONS:
            raise ValueError("ACP evidence direction is invalid")

        with self._lock:
            event = self._event(
                direction,
                message,
                terminal_auth_injected=terminal_auth_injected,
            )
            line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            encoded_bytes = len(line.encode("utf-8"))
            if self._written_bytes + encoded_bytes > _MAX_TRACE_BYTES:
                raise ValueError("ACP evidence trace exceeds four MiB")
            self._stream.write(line)
            self._stream.flush()
            self._written_bytes += encoded_bytes

    def _event(
        self,
        direction: str,
        message: Mapping[str, Any],
        *,
        terminal_auth_injected: bool,
    ) -> dict[str, Any]:
        """Build one event while holding the pending-request lock."""
        event: dict[str, Any] = {"direction": direction}
        method = message.get("method")
        request_id = _request_id(message.get("id"))
        if isinstance(method, str):
            if not method:
                raise ValueError("ACP evidence method is empty")
            event["method"] = method
            if request_id is not None:
                event["id"] = request_id
                key = (direction, request_id)
                if key in self._pending:
                    raise ValueError("ACP evidence reused a pending request id")
                if len(self._pending) >= _MAX_PENDING_REQUESTS:
                    raise ValueError("ACP evidence has too many pending requests")
                self._pending[key] = method
            params = message.get("params")
            if method == "initialize" and isinstance(params, Mapping):
                event["protocol_version"] = params.get("protocolVersion")
                event["terminal_auth_injected"] = terminal_auth_injected
                client_info = params.get("clientInfo")
                if isinstance(client_info, Mapping):
                    event["client_info"] = {
                        key: client_info.get(key)
                        for key in ("name", "title", "version")
                        if isinstance(client_info.get(key), str)
                    }
                client_capabilities = params.get("clientCapabilities")
                if isinstance(client_capabilities, Mapping):
                    auth = client_capabilities.get("auth")
                    meta = client_capabilities.get("_meta")
                    event["terminal_auth_capable"] = (
                        isinstance(auth, Mapping) and auth.get("terminal") is True
                    ) or (isinstance(meta, Mapping) and meta.get("terminal-auth") is True)
            elif method == "session/prompt":
                length, digest = _prompt_fingerprint(params)
                event["prompt_bytes"] = length
                event["prompt_sha256"] = digest
            return event
        if request_id is None:
            raise ValueError("ACP evidence message has neither method nor response id")

        event["id"] = request_id
        response_to = self._pending.pop((_opposite(direction), request_id), None)
        if response_to is None:
            raise ValueError("ACP evidence response id has no pending request")
        event["response_to"] = response_to
        event["error"] = "error" in message
        result = message.get("result")
        if isinstance(result, Mapping):
            if response_to == "initialize":
                event["protocol_version"] = result.get("protocolVersion")
                agent_info = result.get("agentInfo")
                if isinstance(agent_info, Mapping):
                    event["agent_info"] = {
                        key: agent_info.get(key)
                        for key in ("name", "version")
                        if isinstance(agent_info.get(key), str)
                    }
                agent_capabilities = result.get("agentCapabilities")
                if isinstance(agent_capabilities, Mapping):
                    mcp_capabilities = agent_capabilities.get("mcpCapabilities")
                    if isinstance(mcp_capabilities, Mapping):
                        event["mcp_capabilities"] = {
                            key: mcp_capabilities.get(key) is True for key in ("http", "sse")
                        }
                auth_methods = result.get("authMethods")
                event["terminal_auth_method"] = _has_terminal_auth_method(auth_methods)
            elif response_to == "session/new":
                event["session_id_present"] = isinstance(result.get("sessionId"), str)
            elif response_to == "session/prompt":
                event["stop_reason"] = result.get("stopReason")
        return event


def _relay(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    direction: str,
    trace: TraceWriter,
    failures: queue.SimpleQueue[str],
) -> None:
    """Copy complete JSONL messages between two ACP peers."""
    try:
        while line := source.readline(_MAX_LINE_BYTES + 1):
            forwarded, terminal_auth_injected = (
                _forward_client_line(line) if direction == "client_to_agent" else (line, False)
            )
            trace.record(
                direction,
                line,
                terminal_auth_injected=terminal_auth_injected,
            )
            destination.write(forwarded)
            destination.flush()
    except (BrokenPipeError, ValueError) as exc:
        failure = f"ACP trace proxy refused {direction} stream: {exc}"
        failures.put(failure)
        print(failure, file=sys.stderr, flush=True)
    finally:
        try:
            destination.close()
        except BrokenPipeError:
            pass


def _copy_stderr(source: BinaryIO) -> None:
    """Forward OpenCode diagnostics without mixing them into ACP stdout."""
    while chunk := source.read(8192):
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


def main() -> int:
    """Run the exact OpenCode ACP child and return its exit status."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--opencode-bin", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args()

    if not args.opencode_bin.is_file() or not os.access(args.opencode_bin, os.X_OK):
        parser.error("--opencode-bin must be an executable regular file")
    if not args.cwd.is_dir():
        parser.error("--cwd must be a directory")
    args.trace.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    trace = TraceWriter(args.trace)
    failures: queue.SimpleQueue[str] = queue.SimpleQueue()
    process = subprocess.Popen(  # nosec B603
        [str(args.opencode_bin), "acp", "--cwd", str(args.cwd)],
        cwd=args.cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        trace.close()
        raise RuntimeError("OpenCode ACP pipes were not created")

    threads = (
        threading.Thread(
            target=_relay,
            args=(sys.stdin.buffer, process.stdin),
            kwargs={
                "direction": "client_to_agent",
                "trace": trace,
                "failures": failures,
            },
            daemon=True,
        ),
        threading.Thread(
            target=_relay,
            args=(process.stdout, sys.stdout.buffer),
            kwargs={
                "direction": "agent_to_client",
                "trace": trace,
                "failures": failures,
            },
            daemon=True,
        ),
        threading.Thread(target=_copy_stderr, args=(process.stderr,), daemon=True),
    )
    for thread in threads:
        thread.start()
    try:
        while process.poll() is None:
            try:
                failures.get(timeout=0.05)
            except queue.Empty:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return 70
        return process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=2)
        trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
