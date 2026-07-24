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
import ssl
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from synapse_channel.core.errors import SynapseError

CLIENT_NAME = "synapse-stdlib-http-client"
"""Identity of this independent client for receipts."""

CLIENT_VERSION = "1"
"""Client version string recorded in receipts."""

RECEIPT_SCHEMA = "synapse.a2a_interop_trace.v1"
"""Stable schema id for machine-readable interop receipts."""


class A2AInteropTraceError(SynapseError, RuntimeError):
    """Raised when an interop step fails against the live bridge."""

    code = "a2a_interop_trace"


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
    scheme: str = "http",
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """Issue one HTTP(S) request and return status plus JSON or text body."""
    payload = b""
    req_headers = {"Accept": "application/json", **dict(headers or {})}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers["Content-Length"] = str(len(payload))
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    use_tls = scheme == "https"
    if use_tls:
        context = ssl_context if ssl_context is not None else ssl.create_default_context()
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=context
        )
    else:
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


def parse_endpoint(url: str) -> tuple[str, str, int, str]:
    """Return ``(scheme, host, port, path_prefix)`` from an absolute endpoint URL.

    Parameters
    ----------
    url : str
        Absolute ``http://`` or ``https://`` URL of the bridge root
        (e.g. ``https://127.0.0.1:8877``).

    Returns
    -------
    tuple[str, str, int, str]
        Scheme (``http`` or ``https``), host, port, and optional path prefix
        (empty when the URL is the origin).

    Raises
    ------
    ValueError
        When the URL scheme is not HTTP or HTTPS.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(
            f"a2a interop trace supports http:// and https:// endpoints only, got {url!r}"
        )
    host = parsed.hostname or "127.0.0.1"
    default_port = 443 if scheme == "https" else 80
    port = int(parsed.port or default_port)
    prefix = (parsed.path or "").rstrip("/")
    return scheme, host, port, prefix


def run_local_interop_trace(
    *,
    host: str = "127.0.0.1",
    port: int = 8877,
    path_prefix: str = "",
    token: str | None = None,
    message_text: str = "synapse interop probe",
    timeout: float = 5.0,
    scheme: str = "http",
    ssl_context: ssl.SSLContext | None = None,
    ca_file: str | Path | None = None,
    tls_insecure: bool = False,
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
    scheme : str, optional
        ``http`` (default) or ``https`` for native TLS bridges.
    ssl_context : ssl.SSLContext or None, optional
        Explicit TLS context. When omitted and ``scheme`` is ``https``, a
        default context is built from ``ca_file`` / ``tls_insecure``.
    ca_file : str or pathlib.Path or None, optional
        PEM trust anchor for server certificate verification.
    tls_insecure : bool, optional
        When true, skip certificate verification (local self-signed drills only).

    Returns
    -------
    dict[str, Any]
        Machine-readable interop receipt (discovery + task lifecycle).

    Raises
    ------
    A2AInteropTraceError
        When a step fails (non-OK status or missing fields).
    ValueError
        When ``scheme`` is not ``http`` or ``https``.
    """
    normalised_scheme = scheme.lower().strip() or "http"
    if normalised_scheme not in {"http", "https"}:
        raise ValueError(f"unsupported interop scheme: {scheme!r}")
    context = ssl_context
    if normalised_scheme == "https" and context is None:
        context = ssl.create_default_context()
        if tls_insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif ca_file is not None:
            context.load_verify_locations(cafile=str(ca_file))
    prefix = path_prefix.rstrip("/")
    started = time.time()
    if prefix:
        card_path = f"{prefix}/.well-known/agent-card.json"
    else:
        card_path = "/.well-known/agent-card.json"
    status, card = _request(
        host,
        port,
        "GET",
        card_path,
        token=None,
        timeout=timeout,
        scheme=normalised_scheme,
        ssl_context=context,
    )
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
        host,
        port,
        "POST",
        send_path,
        body=send_body,
        token=token,
        timeout=timeout,
        scheme=normalised_scheme,
        ssl_context=context,
    )
    if status != 200 or not isinstance(send_result, dict):
        raise A2AInteropTraceError(f"message:send failed: HTTP {status} body={send_result!r}")
    task = send_result.get("task")
    if not isinstance(task, dict) or not task.get("id"):
        raise A2AInteropTraceError(f"message:send missing task id: {send_result!r}")
    task_id = str(task["id"])
    state = str((task.get("status") or {}).get("state") or "")
    # Message body must survive the round-trip (not merely HTTP 200).
    send_history = task.get("history")
    history_texts: list[str] = []
    if isinstance(send_history, list):
        for item in send_history:
            if not isinstance(item, dict):
                continue
            for part in item.get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    history_texts.append(str(part["text"]))
    if message_text not in history_texts:
        # Status message may carry the user part when history is projected later.
        status_message = (
            (task.get("status") or {}).get("message")
            if isinstance(task.get("status"), dict)
            else None
        )
        status_parts = status_message.get("parts") if isinstance(status_message, dict) else None
        status_texts = [
            str(part["text"])
            for part in (status_parts or [])
            if isinstance(part, dict) and part.get("text")
        ]
        if message_text not in status_texts and message_text not in history_texts:
            raise A2AInteropTraceError(
                f"message:send task missing sent text {message_text!r} in history/status"
            )

    get_path = f"{prefix}/tasks/{task_id}" if prefix else f"/tasks/{task_id}"
    status, got = _request(
        host,
        port,
        "GET",
        get_path,
        token=token,
        timeout=timeout,
        scheme=normalised_scheme,
        ssl_context=context,
    )
    if status != 200 or not isinstance(got, dict):
        raise A2AInteropTraceError(f"GET task failed: HTTP {status} body={got!r}")
    got_id = str(got.get("id") or (got.get("task") or {}).get("id") or "")
    if got_id and got_id != task_id:
        raise A2AInteropTraceError(f"task id mismatch: sent {task_id!r} got {got_id!r}")

    finished = time.time()
    tls_dimension = "recorded" if normalised_scheme == "https" else "not_exercised"
    limitations = [
        "Local independent HTTP+JSON client only; not a third-party A2A SDK.",
        "Webhook and durable-history replay receipts remain external.",
    ]
    if normalised_scheme == "http":
        limitations.append(
            "Plaintext HTTP path only; use scheme=https against native TLS serve for TLS receipts."
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": finished,
        "duration_seconds": round(finished - started, 3),
        "client": {"name": CLIENT_NAME, "version": CLIENT_VERSION, "stack": "http.client"},
        "endpoint": {
            "scheme": normalised_scheme,
            "host": host,
            "port": port,
            "path_prefix": prefix or "/",
            "url": f"{normalised_scheme}://{host}:{port}{prefix or ''}",
        },
        "auth_mode": "bearer" if token else "none",
        "discovery": {
            "path": card_path,
            "http_status": 200,
            "agent_card_name": str(card.get("name") or ""),
            "protocol_binding": _first_binding(card),
            "version": str(card.get("version") or ""),
            "url_scheme": normalised_scheme,
        },
        "task_lifecycle": {
            "message_id": message_id,
            "task_id": task_id,
            "message_text": message_text,
            "send_http_status": 200,
            "observed_state_after_send": state,
            "get_http_status": 200,
            "get_path": get_path,
        },
        "dimensions": {
            "discovery": "recorded",
            "task_lifecycle": "recorded",
            "webhook": "not_exercised",
            "proxy_tls": tls_dimension,
            "replay_subscription": "not_exercised",
            "threat_model": "not_exercised",
        },
        "limitations": limitations,
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
