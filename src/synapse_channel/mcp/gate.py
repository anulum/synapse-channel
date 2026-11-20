# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the stable-bearer gate in front of the authenticated HTTP MCP listener
"""Serve ``synapse mcp-gate``: check a stable bearer, then forward with the seat's token.

Every request must carry a stable bearer whose digest matches one seat. The
gate then forwards that one request, with an allow-listed set of headers and the
seat's current issuer token, to the pinned loopback listener, which still checks
the token, the seat binding and the grants. A session id is never a credential.
The gate is meant to be reachable from the internet through an operator's tunnel,
so its framing is strict, its deadlines are total and its resources are bounded.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import ssl
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from synapse_channel.mcp.gate_config import (
    BEARER_WARNING,
    GateConfig,
    GateConfigError,
    GateSeat,
    bearer_digest,
    grant_file_identity,
    load_bearer,
)

MAX_HEAD = 65536
"""The largest request or response head, in bytes."""
MAX_BODY = 1 << 20
"""The largest request body, in bytes."""
MAX_RESPONSE = 16 << 20
"""The largest buffered response body, in bytes."""
HEAD_DEADLINE = 10.0
"""Seconds for the TLS handshake and for the whole request head."""
BODY_DEADLINE = 30.0
"""Seconds for the whole request body."""
STREAM_IDLE = 120.0
"""Seconds an event stream may stay silent."""
UPSTREAM_DEADLINE = 60.0
"""Seconds for the upstream connection and for a buffered response."""
MAX_CONNECTIONS = 32
"""Connections served at once; a further one is closed at once."""
MAX_SESSIONS = 1024
"""Session ids remembered, least recently used first out."""
FAILURE_WINDOW = 10.0
"""Seconds over which failed authentications are counted."""
FAILURE_LIMIT = 20
"""Failures in the window after which each refusal is delayed."""
FAILURE_DELAY = 1.0
"""Seconds each refusal waits once the limit is passed."""

FORWARDED_HEADERS = frozenset(
    {
        b"content-type",
        b"accept",
        b"mcp-session-id",
        b"mcp-protocol-version",
        b"last-event-id",
        b"user-agent",
    }
)
"""The only client headers the gate forwards; it sets Host, Authorization and Content-Length."""

_LOG = logging.getLogger("synapse.mcp.gate")
_MAX_BODY_DIGITS = len(str(MAX_BODY))
_TOKEN_CHARS = frozenset(
    b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class Refusal(Exception):
    """A request the gate answers with ``status`` and then closes."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


_REASONS = {
    400: b"Bad Request",
    401: b"Unauthorized",
    413: b"Content Too Large",
    431: b"Request Header Fields Too Large",
    502: b"Bad Gateway",
    503: b"Service Unavailable",
}


@dataclass(frozen=True)
class Request:
    """A parsed request head that passed the framing rules."""

    method: bytes
    target: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    length: int | None

    def values(self, name: bytes) -> list[bytes]:
        """Return the values of one header, in order."""
        return [value for key, value in self.headers if key == name]


def _header_lines(head: bytes) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    if b"\x00" in head:
        raise Refusal(400, "NUL in the request head")
    lines = head.split(b"\r\n")
    if any(b"\r" in line or b"\n" in line for line in lines):
        raise Refusal(400, "bare CR or LF in the request head")
    headers: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        if line[:1] in {b" ", b"\t"}:
            raise Refusal(400, "folded header line")
        name, colon, value = line.partition(b":")
        if not colon or not name or not set(name) <= _TOKEN_CHARS:
            raise Refusal(400, "malformed header line")
        headers.append((name.lower(), value.strip(b" \t")))
    return lines[0], headers


def _body_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    codings = [value for key, value in headers if key == b"transfer-encoding"]
    lengths = [value for key, value in headers if key == b"content-length"]
    if codings:
        if lengths or [value.lower() for value in codings] != [b"chunked"]:
            raise Refusal(400, "transfer framing refused")
        return None
    if not lengths:
        return 0
    if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > _MAX_BODY_DIGITS:
        raise Refusal(400, "content-length invalid")
    if int(lengths[0]) > MAX_BODY:
        raise Refusal(413, "body too large")
    return int(lengths[0])


def parse_request(head: bytes) -> Request:
    """Parse one request head, refusing every framing the gate does not accept."""
    request_line, headers = _header_lines(head)
    parts = request_line.split(b" ")
    if len(parts) != 3 or parts[2] not in {b"HTTP/1.1", b"HTTP/1.0"}:
        raise Refusal(400, "malformed request line")
    method, target = parts[0], parts[1]
    if not method or not set(method) <= _TOKEN_CHARS or method in {b"CONNECT", b"TRACE"}:
        raise Refusal(400, "method refused")
    if not target.startswith(b"/") or target.startswith(b"//"):
        raise Refusal(400, "only origin-form targets are accepted")
    if sum(1 for key, _ in headers if key == b"host") > 1:
        raise Refusal(400, "duplicate Host")
    return Request(method, target, tuple(headers), _body_length(headers))


def presented_bearer(request: Request) -> bytes:
    """Return the bearer of the one Authorization header, or empty bytes."""
    values = request.values(b"authorization")
    if len(values) != 1:
        return b""
    scheme, _, credential = values[0].partition(b" ")
    if scheme.lower() != b"bearer":
        return b""
    return credential.strip().strip(b"\"'")


def upstream_head(request: Request, token: bytes, public_host: str, length: int) -> bytes:
    """Return the request head sent upstream: allow-listed headers plus the gate's own."""
    lines = [request.method + b" " + request.target + b" HTTP/1.1"]
    for name, value in request.headers:
        if name in FORWARDED_HEADERS:
            lines.append(name + b": " + value)
    lines += [
        b"Host: " + public_host.encode("ascii"),
        b"Authorization: Bearer " + token,
        b"Content-Length: " + str(length).encode("ascii"),
        b"Connection: close",
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n"


def response_status(head: bytes) -> int:
    """Return the status code of a response head; refuse a malformed one."""
    parts = head.split(b"\r\n", 1)[0].split(b" ", 2)
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/1.") or not parts[1].isdigit():
        raise Refusal(502, "malformed upstream response")
    return int(parts[1])


def response_values(head: bytes, name: bytes) -> list[bytes]:
    """Return the values of one response header."""
    values = []
    for line in head.split(b"\r\n")[1:]:
        key, _, value = line.partition(b":")
        if key.strip().lower() == name:
            values.append(value.strip())
    return values


def client_response_head(head: bytes, *, length: int | None) -> bytes:
    """Remove WWW-Authenticate; with ``length``, re-frame the response as one closed body."""
    lines = head.split(b"\r\n")
    framing = (
        {b"content-length", b"transfer-encoding", b"connection"} if length is not None else set()
    )
    kept = [lines[0]]
    for line in lines[1:]:
        name = line.partition(b":")[0].strip().lower()
        if line and name != b"www-authenticate" and name not in framing:
            kept.append(line)
    if length is not None:
        kept += [b"Content-Length: " + str(length).encode("ascii"), b"Connection: close"]
    return b"\r\n".join(kept) + b"\r\n\r\n"


def error_response(status: int) -> bytes:
    """Return a closed JSON error response that names no detail."""
    body = b'{"error":"' + _REASONS[status].lower().replace(b" ", b"_") + b'"}\n'
    return (
        b"HTTP/1.1 " + str(status).encode("ascii") + b" " + _REASONS[status] + b"\r\n"
        b"Content-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode("ascii")
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )


class SessionTable:
    """Defence in depth: refuse a session id seen with another seat.

    An unknown or evicted session is forwarded with the requesting seat's token,
    and the upstream listener decides, so the table's bound never becomes an
    authentication rule.
    """

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self._limit = limit
        self._seats: OrderedDict[bytes, str] = OrderedDict()

    def check(self, session: bytes, seat: str) -> None:
        """Refuse ``session`` when the table bound it to another seat."""
        bound = self._seats.get(session)
        if bound is not None and bound != seat:
            raise Refusal(401, "session of another seat")

    def bind(self, session: bytes, seat: str) -> None:
        """Remember which seat opened ``session``."""
        self._seats[session] = seat
        self._seats.move_to_end(session)
        while len(self._seats) > self._limit:
            self._seats.popitem(last=False)

    def forget(self, session: bytes) -> None:
        """Forget a deleted session."""
        self._seats.pop(session, None)


@dataclass
class FailureThrottle:
    """Delay refusals once authentication fails often, so the gate cannot be flooded."""

    clock: Callable[[], float] = time.monotonic
    failures: deque[float] = field(default_factory=deque)

    def record(self) -> float:
        """Record one failure and return how long to delay its refusal."""
        now = self.clock()
        self.failures.append(now)
        while self.failures and now - self.failures[0] > FAILURE_WINDOW:
            self.failures.popleft()
        return FAILURE_DELAY if len(self.failures) > FAILURE_LIMIT else 0.0


def resolve_seat(config: GateConfig, presented: bytes, *, now: datetime) -> GateSeat:
    """Return the seat whose bearer digest matches; compare against every seat.

    Raises
    ------
    Refusal
        401 for no bearer, no match or an expired bearer.
    """
    digest = bearer_digest(presented).encode("ascii")
    match: GateSeat | None = None
    expired = False
    for seat in config.seats:
        try:
            record = load_bearer(seat.bearer_file)
        except GateConfigError:
            continue  # a missing or broken record revokes the seat
        if hmac.compare_digest(digest, record.sha256.encode("ascii")) and presented:
            match, expired = seat, now >= record.expires_at
            if not expired and record.expires_at - now <= BEARER_WARNING:
                _LOG.warning("bearer of %s expires at %s; rotate it", seat.seat, record.expires_at)
    if match is None or expired:
        raise Refusal(401, "bearer expired" if expired else "unauthorized")
    return match


def pinned_upstream_context() -> ssl.SSLContext:
    """Return a client context whose trust comes from the certificate pin alone."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def check_pin(certificate: bytes | None, pin: str) -> None:
    """Refuse an upstream whose certificate is not the pinned one, before any request byte."""
    if certificate is None or not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), pin):
        raise Refusal(502, "upstream identity mismatch")


@dataclass
class GateState:
    """What the gate keeps between connections."""

    config: GateConfig
    sessions: SessionTable = field(default_factory=SessionTable)
    throttle: FailureThrottle = field(default_factory=FailureThrottle)
    open_connections: int = 0
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    grant_identity: tuple[int, int, int] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Remember the grant file that start-up already accepted."""
        self.grant_identity = grant_file_identity(self.config.grant_file)
