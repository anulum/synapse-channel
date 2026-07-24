# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound A2A HTTP(S) client
"""Outbound Agent2Agent client for a **second** peer (not self-serve only).

Uses stdlib :mod:`http.client` only. Discovers an Agent Card, sends a user
message, and fetches the resulting task — the independent external-server path
previously recorded as a product gap.
"""

from __future__ import annotations

import http.client
import json
import ssl
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from synapse_channel.a2a import JsonMap
from synapse_channel.core.errors import SynapseError


class A2AClientError(SynapseError, RuntimeError):
    """Raised when an outbound A2A client step fails."""

    code = "a2a_client"


def _request(
    *,
    scheme: str,
    host: str,
    port: int,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    token: str | None = None,
    timeout: float = 10.0,
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
    if scheme == "https":
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


def parse_a2a_endpoint(url: str) -> tuple[str, str, int, str]:
    """Return ``(scheme, host, port, path_prefix)`` from an absolute endpoint URL."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"a2a client supports http:// and https:// only, got {url!r}")
    host = parsed.hostname or "127.0.0.1"
    default_port = 443 if scheme == "https" else 80
    port = int(parsed.port or default_port)
    prefix = (parsed.path or "").rstrip("/")
    return scheme, host, port, prefix


class A2AOutboundClient:
    """Outbound client against a remote A2A HTTP+JSON peer.

    Parameters
    ----------
    endpoint_url : str
        Absolute base URL of the peer (e.g. ``http://127.0.0.1:8877``).
    token : str or None, optional
        Bearer token for protected routes.
    timeout : float, optional
        Per-request timeout in seconds.
    ssl_context : ssl.SSLContext or None, optional
        TLS context for ``https`` endpoints.
    ca_file : str or pathlib.Path or None, optional
        PEM trust anchor when building a default HTTPS context.
    tls_insecure : bool, optional
        Skip certificate verification for local self-signed drills only.
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
        ca_file: str | Path | None = None,
        tls_insecure: bool = False,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.scheme, self.host, self.port, self.path_prefix = parse_a2a_endpoint(self.endpoint_url)
        self._ssl_context = ssl_context
        if self.scheme == "https" and self._ssl_context is None:
            self._ssl_context = ssl.create_default_context()
            if tls_insecure:
                self._ssl_context.check_hostname = False
                self._ssl_context.verify_mode = ssl.CERT_NONE
            elif ca_file is not None:
                self._ssl_context.load_verify_locations(cafile=str(ca_file))

    def _path(self, suffix: str) -> str:
        if self.path_prefix:
            return f"{self.path_prefix}{suffix}"
        return suffix

    def get_agent_card(self) -> JsonMap:
        """GET ``/.well-known/agent-card.json`` from the peer."""
        status, body = _request(
            scheme=self.scheme,
            host=self.host,
            port=self.port,
            method="GET",
            path=self._path("/.well-known/agent-card.json"),
            token=None,
            timeout=self.timeout,
            ssl_context=self._ssl_context,
        )
        if status != 200 or not isinstance(body, dict):
            raise A2AClientError(f"agent-card discovery failed: HTTP {status} body={body!r}")
        return body

    def send_message(
        self,
        text: str,
        *,
        message_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> JsonMap:
        """POST ``/message:send`` with a ROLE_USER text part.

        Returns
        -------
        dict[str, Any]
            The SendMessageResponse body (``task`` and/or ``message``).
        """
        mid = message_id or f"outbound-{uuid.uuid4().hex[:12]}"
        message: JsonMap = {
            "messageId": mid,
            "role": "ROLE_USER",
            "parts": [{"text": text, "mediaType": "text/plain"}],
        }
        if metadata:
            message["metadata"] = dict(metadata)
        status, body = _request(
            scheme=self.scheme,
            host=self.host,
            port=self.port,
            method="POST",
            path=self._path("/message:send"),
            body={"message": message},
            token=self.token,
            timeout=self.timeout,
            ssl_context=self._ssl_context,
            headers={"A2A-Version": "1.0"},
        )
        if status != 200 or not isinstance(body, dict):
            raise A2AClientError(f"message:send failed: HTTP {status} body={body!r}")
        return body

    def get_task(self, task_id: str) -> JsonMap:
        """GET ``/tasks/{task_id}`` from the peer."""
        status, body = _request(
            scheme=self.scheme,
            host=self.host,
            port=self.port,
            method="GET",
            path=self._path(f"/tasks/{task_id}"),
            token=self.token,
            timeout=self.timeout,
            ssl_context=self._ssl_context,
            headers={"A2A-Version": "1.0"},
        )
        if status != 200 or not isinstance(body, dict):
            raise A2AClientError(f"GET task failed: HTTP {status} body={body!r}")
        return body

    def discover_send_get(
        self,
        text: str,
        *,
        message_id: str | None = None,
    ) -> JsonMap:
        """Discover Agent Card, send ``text``, then GET the resulting task.

        Returns
        -------
        dict[str, Any]
            Receipt with card name, task id, send response, and fetched task.
        """
        card = self.get_agent_card()
        sent = self.send_message(text, message_id=message_id)
        task = sent.get("task")
        if not isinstance(task, dict) or not task.get("id"):
            # Direct Message response profile — no task to GET.
            return {
                "endpoint_url": self.endpoint_url,
                "scheme": self.scheme,
                "agent_card_name": str(card.get("name") or ""),
                "send_response": sent,
                "task_id": None,
                "task": None,
                "message_text": text,
            }
        task_id = str(task["id"])
        got = self.get_task(task_id)
        return {
            "endpoint_url": self.endpoint_url,
            "scheme": self.scheme,
            "agent_card_name": str(card.get("name") or ""),
            "send_response": sent,
            "task_id": task_id,
            "task": got,
            "message_text": text,
        }


def resolve_grpc_interface_url(card: Mapping[str, Any]) -> str | None:
    """Return the first GRPC interface URL from an Agent Card, if any."""
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list):
        return None
    for item in interfaces:
        if not isinstance(item, Mapping):
            continue
        binding = str(item.get("protocolBinding") or "").upper()
        if binding in {"GRPC", "HTTP+GRPC", "GRPC+PROTO"}:
            url = item.get("url")
            if url:
                return str(url)
    return None


def join_endpoint(base: str, path: str) -> str:
    """Join a base endpoint URL with a path segment."""
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
