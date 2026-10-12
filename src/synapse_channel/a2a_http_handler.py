# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded HTTP I/O and admission for the Agent2Agent bridge
"""Bounded request I/O, browser/authentication admission and response encoding."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from synapse_channel import a2a_http_protocol as _protocol
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import SSE_MEDIA_TYPE, is_supported_json_media_type
from synapse_channel.core.protocol import loads_bounded

if TYPE_CHECKING:
    from synapse_channel.a2a_server import A2ABridge

MAX_A2A_JSON_BODY_BYTES = 1024 * 1024
"""Upper bound for one JSON request body."""
DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS = 30.0
"""Default wall-clock request read deadline."""
bearer_token_matches = _protocol.bearer_token_matches
origin_allowed = _protocol.origin_allowed
problem_response = _protocol.problem_response


class A2AHTTPHandler(BaseHTTPRequestHandler):
    """Encode A2A responses and admit bounded requests for a bound bridge.

    The serving factory binds ``bridge`` independently on every concrete
    subclass. Host/Origin admission precedes authentication and body parsing.
    """

    bridge: A2ABridge

    def log_message(self, _format: str, *_args: Any) -> None:
        """Silence stdlib access logging; the caller owns process logging."""
        return None

    def _send_json(
        self,
        status: HTTPStatus,
        body: JsonMap,
        *,
        media_type: str = _protocol.HTTP_JSON_MEDIA_TYPE,
    ) -> None:
        raw = json.dumps(_protocol.to_wire_json(body), sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, status: HTTPStatus, body: JsonMap) -> None:
        self._send_sse_events(status, [body])

    def _send_sse_events(self, status: HTTPStatus, bodies: Iterable[JsonMap]) -> None:
        """Send one bounded Server-Sent Events response."""
        raw = b"".join(
            f"data: {json.dumps(_protocol.to_wire_json(body), sort_keys=True)}\n\n".encode()
            for body in bodies
        )
        self.send_response(int(status))
        self.send_header("Content-Type", SSE_MEDIA_TYPE)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _request_read_timeout_seconds(self) -> float:
        """Return the server-configured wall-clock body/header read budget."""
        server = self.server
        timeout = getattr(
            server,
            "request_read_timeout_seconds",
            DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
        )
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            return DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS
        return value if value > 0.0 else DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS

    def _send_read_timeout(self, detail: str = "Request body read timed out") -> None:
        """Refuse a request whose body could not be fully read in time."""
        self._send_json(
            HTTPStatus.REQUEST_TIMEOUT,
            problem_response(
                HTTPStatus.REQUEST_TIMEOUT,
                "Request Timeout",
                detail,
                reason="A2A_HTTP_READ_TIMEOUT",
            ),
        )

    def _read_body_exactly(self, length: int) -> bytes | None:
        """Read exactly ``length`` body bytes under a wall-clock deadline.

        Returns ``None`` after sending a deterministic timeout response when
        the deadline expires, the peer stalls, or the connection closes before
        the declared ``Content-Length`` is satisfied.
        """
        if length <= 0:
            return b""
        deadline = time.monotonic() + self._request_read_timeout_seconds()
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            budget = deadline - time.monotonic()
            if budget <= 0.0:
                self._send_read_timeout()
                return None
            if isinstance(self.connection, socket.socket):
                self.connection.settimeout(budget)
            try:
                chunk = self.rfile.read(remaining)
            except TimeoutError:
                self._send_read_timeout()
                return None
            except OSError:
                self._send_read_timeout("Request body read failed")
                return None
            if not chunk:
                self._send_read_timeout("Request body incomplete")
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_json(self) -> JsonMap | None:
        content_type = self.headers.get("Content-Type", "")
        if not is_supported_json_media_type(content_type):
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                problem_response(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Unsupported Media Type",
                    reason="CONTENT_TYPE_NOT_SUPPORTED",
                ),
            )
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_A2A_JSON_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                problem_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"),
            )
            return None
        raw = self._read_body_exactly(max(length, 0))
        if raw is None:
            return None
        try:
            data = loads_bounded(raw if raw else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                problem_response(HTTPStatus.BAD_REQUEST, "Invalid JSON"),
            )
            return None
        if not isinstance(data, dict):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                problem_response(HTTPStatus.BAD_REQUEST, "Invalid request body"),
            )
            return None
        return data

    def _send_not_found(self, detail: str = "") -> None:
        reason = "TASK_NOT_FOUND" if detail.startswith("Unknown task:") else None
        self._send_json(
            HTTPStatus.NOT_FOUND,
            problem_response(HTTPStatus.NOT_FOUND, "Not Found", detail, reason=reason),
        )

    def _is_authorized(self) -> bool:
        token = self.bridge.auth_token
        if not token:
            return True
        authorization = self.headers.get("Authorization", "") or ""
        return bearer_token_matches(authorization, token)

    def _require_authorized(self) -> bool:
        if self._is_authorized():
            return True
        self._send_json(
            HTTPStatus.UNAUTHORIZED,
            problem_response(HTTPStatus.UNAUTHORIZED, "Unauthorized"),
        )
        return False

    def _require_browser_boundary(self) -> bool:
        """Refuse a request outside the configured Origin/Host boundary.

        Runs before authentication and on every route, including the public
        agent card. Every request must address the exact advertised endpoint
        authority. A present Origin is an independent browser permission and
        must match the configured allow-list.
        """
        origin_headers = self.headers.get_all("Origin") or []
        host_headers = self.headers.get_all("Host") or []
        if (
            len(host_headers) == 1
            and len(origin_headers) <= 1
            and origin_allowed(
                origin_headers[0] if origin_headers else None,
                host_headers[0],
                self.bridge.allowed_origins,
                self.bridge.allowed_authorities,
            )
        ):
            return True
        self._send_json(
            HTTPStatus.FORBIDDEN,
            problem_response(
                HTTPStatus.FORBIDDEN,
                "Forbidden",
                "Origin or Host not allowed",
                reason="ORIGIN_OR_HOST_NOT_ALLOWED",
            ),
        )
        return False

    def _negotiate_protocol_version(self, query: str) -> tuple[bool, str | None]:
        """Validate an explicit A2A version and return the active semantics."""
        requested = _protocol.requested_a2a_version(
            self.headers.get("A2A-Version"),
            parse_qs(query).get("A2A-Version", []),
        )
        if not _protocol.supports_a2a_version(requested):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                problem_response(
                    HTTPStatus.BAD_REQUEST,
                    "Version not supported",
                    f"A2A version {requested!r} is not supported",
                    reason="VERSION_NOT_SUPPORTED",
                    metadata={"requestedVersion": requested or ""},
                ),
            )
            return False, None
        return True, _protocol.SUPPORTED_A2A_VERSION if requested else None
