# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent HTTP client A2A interoperability traces
"""Run an independent-client A2A interop trace against a live bridge.

The client stack is stdlib :mod:`http.client` only. It does **not** import the
A2A request handler path — independence means a second process (or this client
stack) speaking HTTP+JSON to a running bridge. That is enough for a **local**
interop receipt (discovery + task lifecycle). Third-party SDK/public-network
receipts remain external and are not claimed here.
"""

from __future__ import annotations

import http.client
import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CLIENT_NAME = "synapse-stdlib-http-client"
"""Identity of this independent client for receipts."""

CLIENT_VERSION = "1"
"""Client version string recorded in receipts."""

RECEIPT_SCHEMA = "synapse.a2a_interop_trace.v1"
"""Stable schema id for machine-readable interop receipts."""


class A2AInteropTraceError(RuntimeError):
    """Raised when an interop step fails against the live bridge."""


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    token: str | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any] | str]:
    """Issue one HTTP request and return status plus JSON or text body."""
    payload = b""
    req_headers = {"Accept": "application/json", **dict(headers or {})}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers["Content-Length"] = str(len(payload))
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=payload, headers=req_headers)
        response = conn.getresponse()
        raw = response.read()
        status = int(response.status)
    finally:
        conn.close()
    if not raw:
        return status, ""
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, raw.decode("utf-8", errors="replace")


def parse_endpoint(url: str) -> tuple[str, int, str]:
    """Return ``(host, port, path_prefix)`` from an absolute HTTP endpoint URL.

    Parameters
    ----------
    url : str
        Absolute ``http://`` URL of the bridge root (e.g. ``http://127.0.0.1:8877``).

    Returns
    -------
    tuple[str, int, str]
        Host, port, and optional path prefix (empty when the URL is the origin).
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", ""}:
        raise ValueError(f"a2a interop trace supports http:// endpoints only, got {url!r}")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 80)
    prefix = (parsed.path or "").rstrip("/")
    return host, port, prefix


def run_local_interop_trace(
    *,
    host: str = "127.0.0.1",
    port: int = 8877,
    path_prefix: str = "",
    token: str | None = None,
    message_text: str = "synapse interop probe",
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Exercise discovery + message send + task get as an independent client.

    Parameters
    ----------
    host, port : str, int
        Bridge listen address.
    path_prefix : str, optional
        Optional URL path prefix before A2A routes.
    token : str or None, optional
        Bearer token when the bridge requires auth on protected routes.
    message_text : str, optional
        Text part sent via ``POST /message:send``.
    timeout : float, optional
        Per-request timeout in seconds.

    Returns
    -------
    dict[str, Any]
        Machine-readable interop receipt (discovery + task lifecycle).

    Raises
    ------
    A2AInteropTraceError
        When a step fails (non-OK status or missing fields).
    """
    prefix = path_prefix.rstrip("/")
    started = time.time()
    if prefix:
        card_path = f"{prefix}/.well-known/agent-card.json"
    else:
        card_path = "/.well-known/agent-card.json"
    status, card = _request(host, port, "GET", card_path, token=None, timeout=timeout)
    if status != 200 or not isinstance(card, dict):
        raise A2AInteropTraceError(f"discovery failed: HTTP {status} body={card!r}")

    message_id = f"interop-{uuid.uuid4().hex[:12]}"
    send_body = {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": message_text}],
        }
    }
    send_path = f"{prefix}/message:send" if prefix else "/message:send"
    status, send_result = _request(
        host, port, "POST", send_path, body=send_body, token=token, timeout=timeout
    )
    if status != 200 or not isinstance(send_result, dict):
        raise A2AInteropTraceError(f"message:send failed: HTTP {status} body={send_result!r}")
    task = send_result.get("task")
    if not isinstance(task, dict) or not task.get("id"):
        raise A2AInteropTraceError(f"message:send missing task id: {send_result!r}")
    task_id = str(task["id"])
    state = str((task.get("status") or {}).get("state") or "")

    get_path = f"{prefix}/tasks/{task_id}" if prefix else f"/tasks/{task_id}"
    status, got = _request(host, port, "GET", get_path, token=token, timeout=timeout)
    if status != 200 or not isinstance(got, dict):
        raise A2AInteropTraceError(f"GET task failed: HTTP {status} body={got!r}")
    got_id = str(got.get("id") or (got.get("task") or {}).get("id") or "")
    if got_id and got_id != task_id:
        raise A2AInteropTraceError(f"task id mismatch: sent {task_id!r} got {got_id!r}")

    finished = time.time()
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": finished,
        "duration_seconds": round(finished - started, 3),
        "client": {"name": CLIENT_NAME, "version": CLIENT_VERSION, "stack": "http.client"},
        "endpoint": {"host": host, "port": port, "path_prefix": prefix or "/"},
        "auth_mode": "bearer" if token else "none",
        "discovery": {
            "path": card_path,
            "http_status": 200,
            "agent_card_name": str(card.get("name") or ""),
            "protocol_binding": _first_binding(card),
            "version": str(card.get("version") or ""),
        },
        "task_lifecycle": {
            "message_id": message_id,
            "task_id": task_id,
            "send_http_status": 200,
            "observed_state_after_send": state,
            "get_http_status": 200,
            "get_path": get_path,
        },
        "dimensions": {
            "discovery": "recorded",
            "task_lifecycle": "recorded",
            "webhook": "not_exercised",
            "proxy_tls": "not_exercised",
            "replay_subscription": "not_exercised",
            "threat_model": "not_exercised",
        },
        "limitations": [
            "Local independent HTTP+JSON client only; not a third-party A2A SDK.",
            "Webhook, proxy/TLS, and durable-history replay receipts remain external.",
        ],
    }


def _first_binding(card: Mapping[str, Any]) -> str:
    """Return the first protocolBinding from an Agent Card, if present."""
    interfaces = card.get("supportedInterfaces")
    if isinstance(interfaces, list) and interfaces:
        first = interfaces[0]
        if isinstance(first, Mapping):
            return str(first.get("protocolBinding") or "")
    return ""


def write_interop_receipt(path: str | Path, receipt: Mapping[str, Any]) -> Path:
    """Write a receipt JSON document with owner-readable formatting."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(receipt), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return target
