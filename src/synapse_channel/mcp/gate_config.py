# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — configuration, bearers and seat tokens of the MCP bearer gate
"""The configuration, the stable bearers and the seat tokens of ``synapse mcp-gate``.

The gate stands in front of the authenticated HTTP MCP listener for a client that
can keep only one static secret. Each seat has a stable bearer, stored here only
as its SHA-256 digest with an issue and an expiry time, and a token file that the
operator's renewal job rewrites with the seat's current issuer token. The gate
holds no issuer key and grants nothing the seat's token does not already grant.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes

GATE_CONFIG_SCHEMA = "synapse-mcp-gate.v1"
"""The schema name every gate configuration carries."""

BEARER_SCHEMA = "synapse-mcp-gate-bearer.v1"
"""The schema name every bearer record carries."""

BEARER_LIFETIME = timedelta(days=90)
"""How long a new stable bearer is valid."""

BEARER_WARNING = timedelta(days=14)
"""How long before its expiry a bearer is reported as due for rotation."""

MAX_GATE_SEATS = 32
"""The most seats one gate serves."""

MAX_SEAT_TOOLS = 32
"""The most tools one seat may list."""

_MAX_CONFIG_BYTES = 65536
_MAX_SECRET_BYTES = 8192
_SEAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._@-]*")
_PUBLIC_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TOOL = re.compile(r"synapse_[a-z_]{1,48}")
_TOKEN = re.compile(rb"[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+")
_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "listen",
        "upstream",
        "upstream_cert_sha256",
        "public_host",
        "tls_cert_file",
        "tls_key_file",
        "grant_file",
        "seats",
    }
)
_SEAT_FIELDS = frozenset({"seat", "bearer_file", "token_file", "tools"})
_BEARER_FIELDS = frozenset({"schema", "sha256", "issued_at", "expires_at"})


class GateConfigError(SynapseError, ValueError):
    """A gate configuration or bearer record the gate refuses; messages hold no secret."""

    code = "mcp_gate_config"


class SeatTokenError(SynapseError, RuntimeError):
    """A seat's issuer token is missing, malformed or expired; the gate answers 503."""

    code = "mcp_gate_seat_token"


@dataclass(frozen=True)
class GateSeat:
    """One seat behind the gate, with its bearer record, its token file and its tools.

    ``tools`` is the operator's explicit list of the tools this seat may hold.
    The gate refuses to start when the listener grants the seat any other tool,
    so a tool that changes work is present only when the operator lists it.
    """

    seat: str
    bearer_file: Path
    token_file: Path
    tools: frozenset[str]


@dataclass(frozen=True)
class GateConfig:
    """A validated gate configuration."""

    listen_host: str
    listen_port: int
    upstream_host: str
    upstream_port: int
    upstream_cert_sha256: str
    public_host: str
    tls_cert_file: Path
    tls_key_file: Path
    grant_file: Path
    seats: tuple[GateSeat, ...]


@dataclass(frozen=True)
class BearerRecord:
    """The digest and the validity window of one stable bearer."""

    sha256: str
    issued_at: datetime
    expires_at: datetime


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GateConfigError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: Path, purpose: str) -> Mapping[str, object]:
    try:
        raw = read_owner_only_file_bytes(path, purpose=purpose, max_bytes=_MAX_CONFIG_BYTES)
    except (OSError, SecurePathError) as exc:
        raise GateConfigError(f"cannot read the {purpose}: {exc}") from exc
    try:
        document = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateConfigError(f"the {purpose} must be a UTF-8 JSON object") from exc
    if not isinstance(document, dict):
        raise GateConfigError(f"the {purpose} must be a UTF-8 JSON object")
    return document


def _absolute(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise GateConfigError(f"{field} must be an absolute path")
    return Path(value)


def _loopback(host: object, field: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(str(host))
    except ValueError:
        raise GateConfigError(f"{field} must be a loopback address") from None
    if not isinstance(host, str) or not address.is_loopback:
        raise GateConfigError(f"{field} must be a loopback address")
    return host


def _port(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise GateConfigError(f"{field} must be a port from 1 to 65535")
    return value


def _listen(value: object) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"host", "port"}:
        raise GateConfigError('listen must be {"host": <loopback>, "port": <port>}')
    return _loopback(value["host"], "listen.host"), _port(value["port"], "listen.port")


def _upstream(value: object) -> tuple[str, int]:
    parts = urlsplit(value) if isinstance(value, str) else None
    if (
        parts is None
        or parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        raise GateConfigError("upstream must be https://<loopback>:<port> without a path")
    try:
        port = parts.port
    except ValueError:
        port = None
    return _loopback(parts.hostname, "upstream host"), _port(port, "upstream port")


def _tools(value: object, field: str) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_SEAT_TOOLS
        or not all(isinstance(tool, str) and _TOOL.fullmatch(tool) for tool in value)
        or len(set(value)) != len(value)
    ):
        raise GateConfigError(
            f"{field} must list 1 to {MAX_SEAT_TOOLS} distinct synapse_ tool names"
        )
    return frozenset(value)


def _seats(value: object) -> tuple[GateSeat, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_GATE_SEATS:
        raise GateConfigError(f"seats must list 1 to {MAX_GATE_SEATS} seats")
    seats: list[GateSeat] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _SEAT_FIELDS:
            raise GateConfigError(
                f"seats[{index}] must be {{seat, bearer_file, token_file, tools}}"
            )
        seat = item["seat"]
        if not isinstance(seat, str) or not _SEAT.fullmatch(seat):
            raise GateConfigError(f"seats[{index}].seat must be a PROJECT/seat identity")
        seats.append(
            GateSeat(
                seat,
                _absolute(item["bearer_file"], f"seats[{index}].bearer_file"),
                _absolute(item["token_file"], f"seats[{index}].token_file"),
                _tools(item["tools"], f"seats[{index}].tools"),
            )
        )
    files = [path for seat in seats for path in (seat.bearer_file, seat.token_file)]
    if len({seat.seat for seat in seats}) != len(seats) or len(set(files)) != len(files):
        raise GateConfigError("each seat, bearer file and token file must appear once")
    return tuple(seats)


def load_gate_config(path: Path) -> GateConfig:
    """Read and validate an owner-only gate configuration.

    Raises
    ------
    GateConfigError
        For a file that is not owner-only or not exactly the documented shape,
        a non-loopback address, an upstream without a certificate pin, or a
        repeated seat or file.
    """
    document = _read_json(path, "gate configuration")
    if set(document) != _CONFIG_FIELDS or document["schema"] != GATE_CONFIG_SCHEMA:
        raise GateConfigError(
            f"the gate configuration must be schema {GATE_CONFIG_SCHEMA} with exactly the "
            "documented fields"
        )
    pin = document["upstream_cert_sha256"]
    if not isinstance(pin, str) or not _DIGEST.fullmatch(pin):
        raise GateConfigError("upstream_cert_sha256 must be 64 lowercase hex digits")
    public_host = document["public_host"]
    if not isinstance(public_host, str) or not _PUBLIC_HOST.fullmatch(public_host):
        raise GateConfigError("public_host must be a host name, optionally with a port")
    listen_host, listen_port = _listen(document["listen"])
    upstream_host, upstream_port = _upstream(document["upstream"])
    return GateConfig(
        listen_host=listen_host,
        listen_port=listen_port,
        upstream_host=upstream_host,
        upstream_port=upstream_port,
        upstream_cert_sha256=pin,
        public_host=public_host,
        tls_cert_file=_absolute(document["tls_cert_file"], "tls_cert_file"),
        tls_key_file=_absolute(document["tls_key_file"], "tls_key_file"),
        grant_file=_absolute(document["grant_file"], "grant_file"),
        seats=_seats(document["seats"]),
    )


def check_seat_grants(config: GateConfig) -> None:
    """Refuse a gate whose seat is not granted, or is granted a tool it does not list.

    Raises
    ------
    GateConfigError
        When the listener's grant file cannot be read, does not grant a gate
        seat, or grants a seat a tool outside that seat's ``tools`` list.
    """
    # The listener's grant model needs the MCP extra; import it only when a gate starts.
    from synapse_channel.mcp.http_config import HttpConfigError, load_http_auth_config

    try:
        grants = load_http_auth_config(config.grant_file)
    except HttpConfigError as exc:
        raise GateConfigError("the listener's grant file cannot be used") from exc
    tools = {
        grant.seat: grant.tools
        for subject in grants.subjects.values()
        for grant in subject.projects.values()
    }
    for seat in config.seats:
        if seat.seat not in tools:
            raise GateConfigError(f"the grant file grants no subject the seat {seat.seat}")
        unlisted = sorted(tools[seat.seat] - seat.tools)
        if unlisted:
            raise GateConfigError(
                f"the seat {seat.seat} holds grants its gate entry does not list: "
                f"{', '.join(unlisted)}"
            )


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise GateConfigError(f"{field} must be an ISO-8601 UTC time ending in Z")
    try:
        return datetime.fromisoformat(value[:-1]).replace(tzinfo=UTC)
    except ValueError:
        raise GateConfigError(f"{field} must be an ISO-8601 UTC time ending in Z") from None


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_bearer(path: Path) -> BearerRecord:
    """Read one owner-only bearer record, which holds a digest and never the secret."""
    document = _read_json(path, "bearer record")
    if set(document) != _BEARER_FIELDS or document["schema"] != BEARER_SCHEMA:
        raise GateConfigError(
            f"a bearer record must be schema {BEARER_SCHEMA} with sha256, issued_at and "
            "expires_at only"
        )
    digest = document["sha256"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise GateConfigError("a bearer record's sha256 must be 64 lowercase hex digits")
    issued = _timestamp(document["issued_at"], "issued_at")
    expires = _timestamp(document["expires_at"], "expires_at")
    if expires <= issued:
        raise GateConfigError("a bearer record must expire after it is issued")
    return BearerRecord(digest, issued, expires)


def check_bearers(config: GateConfig) -> None:
    """Refuse a gate whose bearer record is missing or is not a digest record.

    A file that holds a raw secret instead of its digest fails here, before the
    gate serves anything.
    """
    for seat in config.seats:
        try:
            load_bearer(seat.bearer_file)
        except GateConfigError as exc:
            raise GateConfigError(f"the bearer record of {seat.seat} is unusable: {exc}") from exc


def bearer_digest(presented: bytes) -> str:
    """Return the SHA-256 hex digest the gate compares for a presented bearer."""
    return hashlib.sha256(presented).hexdigest()


def new_bearer(path: Path, *, now: datetime | None = None, replace: bool = False) -> str:
    """Create a 256-bit stable bearer, store its record at ``path`` and return the secret once.

    The record is written to a new owner-only file. With ``replace`` an existing
    record is replaced atomically, which revokes the old bearer.
    """
    secret = secrets.token_urlsafe(32)
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    record = {
        "schema": BEARER_SCHEMA,
        "sha256": bearer_digest(secret.encode("ascii")),
        "issued_at": _format(issued),
        "expires_at": _format(issued + BEARER_LIFETIME),
    }
    payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("ascii")
    target = path if not replace else path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise GateConfigError(
            "a bearer record already exists there; use --replace to rotate it"
        ) from None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    if replace:
        os.replace(target, path)
    return secret


def read_seat_token(path: Path, *, now: datetime | None = None) -> bytes:
    """Return a seat's current issuer token, or raise :class:`SeatTokenError`.

    The token is read at every request, so the renewal job's atomic rewrite is
    picked up at once. Its shape and its ``exp`` claim are checked; its
    signature is the upstream listener's to verify.
    """
    try:
        raw = read_owner_only_file_bytes(path, purpose="seat token", max_bytes=_MAX_SECRET_BYTES)
    except (OSError, SecurePathError) as exc:
        raise SeatTokenError("the seat token cannot be read") from exc
    token = raw.strip()
    match = _TOKEN.fullmatch(token)
    if match is None:
        raise SeatTokenError("the seat token is not an issuer token")
    payload = match.group(1)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4)))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise SeatTokenError("the seat token is not an issuer token") from None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(expiry, bool) or not isinstance(expiry, int):
        raise SeatTokenError("the seat token has no expiry")
    if expiry <= (now or datetime.now(UTC)).timestamp():
        raise SeatTokenError("the seat token has expired")
    return token
