# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A HTTP+JSON protocol projection
"""Project internal A2A state onto the versioned HTTP+JSON wire contract."""

from __future__ import annotations

import hmac
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import urlsplit

from synapse_channel.a2a import JsonMap
from synapse_channel.core.errors import error_code

HTTP_JSON_MEDIA_TYPE: Final = "application/json"
"""Normative response media type for the A2A HTTP+JSON binding."""

SUPPORTED_A2A_VERSION: Final = "1.0"
"""Major/minor A2A version implemented by the advertised HTTP interface."""

ERROR_INFO_TYPE: Final = "type.googleapis.com/google.rpc.ErrorInfo"
ERROR_INFO_DOMAIN: Final = "a2a-protocol.org"

_TIMESTAMP_FIELDS: Final = frozenset({"createdAt", "lastModified", "timestamp", "updatedAt"})

_GOOGLE_STATUS_BY_HTTP: Final[dict[HTTPStatus, str]] = {
    HTTPStatus.BAD_REQUEST: "INVALID_ARGUMENT",
    HTTPStatus.UNAUTHORIZED: "UNAUTHENTICATED",
    HTTPStatus.FORBIDDEN: "PERMISSION_DENIED",
    HTTPStatus.NOT_FOUND: "NOT_FOUND",
    HTTPStatus.CONFLICT: "FAILED_PRECONDITION",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "RESOURCE_EXHAUSTED",
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "INVALID_ARGUMENT",
    HTTPStatus.TOO_MANY_REQUESTS: "RESOURCE_EXHAUSTED",
    HTTPStatus.INTERNAL_SERVER_ERROR: "INTERNAL",
}


def bearer_token_matches(authorization: str, token: str) -> bool:
    """Return whether an Authorization header exactly carries ``token``."""
    return hmac.compare_digest(authorization, f"Bearer {token}")


def describe_a2a_origin_policy(*, allow_origins: tuple[str, ...] = ()) -> dict[str, object]:
    """Return the effective A2A browser Origin/Host policy for operators.

    Parameters
    ----------
    allow_origins : tuple[str, ...]
        Raw ``--allow-origin`` values the operator intends to enable (may be empty).

    Returns
    -------
    dict[str, object]
        Stable mapping: whether opaque ``null`` is rejected, whether the allow-list
        is active, the normalised origins, and that Host authority binding applies
        when the list is non-empty.

    Raises
    ------
    ValueError
        If any supplied origin fails :func:`normalise_origin`.
    """
    normalised = tuple(normalise_origin(origin) for origin in allow_origins)
    return {
        "opaque_null_rejected": True,
        "allow_list_enabled": bool(normalised),
        "allow_origins": list(normalised),
        "host_authority_binding_when_enabled": True,
        "default_allow_list": "off",
    }


def normalise_origin(value: str) -> str:
    """Validate and normalise one concrete HTTP(S) web origin.

    Parameters
    ----------
    value : str
        A web origin such as ``https://ide.example:8443``. Opaque ``null``
        origins are refused because they do not identify one exact principal.

    Returns
    -------
    str
        The lower-cased ``scheme://host[:port]`` value. Origins are compared as
        whole strings, so an entry admits exactly one concrete principal.

    Raises
    ------
    ValueError
        If the value is opaque, malformed, credential-bearing, non-HTTP(S), or
        contains a path, query, or fragment.
    """
    candidate = value.strip()
    if candidate.lower() == "null":
        raise ValueError("opaque 'null' origins cannot be allow-listed")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Origin must be one exact HTTP(S) origin") from exc
    if (
        _has_unsafe_authority_chars(candidate)
        or parsed.netloc.endswith(":")
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Origin must be one exact HTTP(S) origin")
    authority = _format_authority(parsed.hostname, port)
    return f"{parsed.scheme.lower()}://{authority}"


def normalise_authority(value: str) -> str:
    """Validate and normalise one HTTP Host authority without widening it."""
    candidate = value.strip()
    try:
        parsed = urlsplit(f"//{candidate}")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Host must be one exact host[:port] authority") from exc
    if (
        not candidate
        or _has_unsafe_authority_chars(candidate)
        or candidate.endswith(":")
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Host must be one exact host[:port] authority")
    return _format_authority(parsed.hostname, port)


def endpoint_authorities(endpoint_url: str) -> tuple[str, ...]:
    """Return exact Host authorities admitted by one advertised endpoint URL."""
    try:
        parsed = urlsplit(endpoint_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint URL must identify one HTTP(S) authority") from exc
    if (
        _has_unsafe_authority_chars(parsed.netloc)
        or parsed.netloc.endswith(":")
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("endpoint URL must identify one HTTP(S) authority")
    authority = _format_authority(parsed.hostname, port)
    if port is not None:
        return (authority,)
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    return (authority, f"{authority}:{default_port}")


def origin_allowed(
    origin_header: str | None,
    host_header: str | None,
    allowed_origins: tuple[str, ...],
    allowed_authorities: tuple[str, ...],
) -> bool:
    """Decide whether a request passes the optional browser/Host boundary.

    The list is an opt-in hardening against browser-borne requests (DNS
    rebinding, malicious pages calling a loopback bridge): with no list
    configured every request passes unchanged. With a list configured, every
    request must carry the advertised endpoint's exact ``Host`` authority. A
    present ``Origin`` must additionally match one concrete allow-list entry.
    A request without ``Origin`` may pass only through that Host boundary, so a
    hostile DNS-rebinding authority cannot be mistaken for a non-browser client.

    Parameters
    ----------
    origin_header : str or None
        The request's ``Origin`` header, or ``None`` when absent.
    host_header : str or None
        The HTTP Host header carrying the requested authority.
    allowed_origins : tuple of str
        Normalised allow-list entries; empty means the feature is off.
    allowed_authorities : tuple of str
        Exact advertised endpoint authorities accepted in Host.

    Returns
    -------
    bool
        Whether the request may proceed.
    """
    if not allowed_origins:
        return True
    try:
        authority = normalise_authority(host_header or "")
    except ValueError:
        return False
    if authority not in allowed_authorities:
        return False
    if origin_header is None:
        return True
    try:
        origin = normalise_origin(origin_header)
    except ValueError:
        return False
    return origin in allowed_origins


def _format_authority(hostname: str, port: int | None) -> str:
    """Return a lower-case DNS/IPv4/IPv6 authority with an optional port."""
    host = hostname.rstrip(".").lower()
    if not host:
        raise ValueError("authority host must not be empty")
    rendered = f"[{host}]" if ":" in host else host
    return rendered if port is None else f"{rendered}:{port}"


def _has_unsafe_authority_chars(value: str) -> bool:
    """Return whether an authority-bearing value contains delimiter ambiguity."""
    return any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ) or any(character in value for character in (",", "\\"))


def non_negative_int(value: object, *, default: int = 0) -> int:
    """Parse ``value`` as an integer clamped to zero or greater."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def parse_push_config_path(path: str) -> tuple[str, str | None] | None:
    """Parse ``/tasks/{task}/pushNotificationConfigs[/config]`` paths."""
    prefix = "/tasks/"
    marker = "/pushNotificationConfigs"
    if not path.startswith(prefix) or marker not in path:
        return None
    rest = path.removeprefix(prefix)
    task_id, _, tail = rest.partition(marker)
    if not task_id:
        return None
    return task_id, tail.strip("/") or None


def requested_a2a_version(header_value: str | None, query_values: list[str]) -> str | None:
    """Resolve the requested version from the header, then query parameter."""
    if header_value is not None:
        return header_value.strip()
    if query_values:
        return query_values[0].strip()
    return None


def supports_a2a_version(value: str | None) -> bool:
    """Return whether an explicitly requested version is supported.

    Missing or empty values retain the pre-1.0 compatibility path. Explicit
    versions compare only major/minor, so a patch suffix cannot change
    negotiation.
    """
    if not value:
        return True
    components = value.split(".")
    if len(components) < 2 or not all(part.isdigit() for part in components):
        return False
    return ".".join(components[:2]) == SUPPORTED_A2A_VERSION


def error_info_reason(exc: BaseException) -> str | None:
    """Return the standard A2A ErrorInfo reason for a typed failure."""
    code = error_code(exc)
    if code == "a2a_not_found":
        return "TASK_NOT_FOUND"
    if code == "a2a_conflict":
        return "TASK_NOT_CANCELABLE"
    return None


def problem_response(
    status: HTTPStatus,
    title: str,
    detail: str = "",
    *,
    reason: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> JsonMap:
    """Build an AIP-193 error plus transitional RFC 7807 fields.

    The nested ``error`` object is the normative HTTP+JSON representation.
    Existing clients may continue reading the top-level problem fields during
    the compatibility window.
    """
    message = detail or title
    error: JsonMap = {
        "code": int(status),
        "status": _GOOGLE_STATUS_BY_HTTP.get(status, status.name),
        "message": message,
        "details": [],
    }
    if reason:
        error["details"] = [
            {
                "@type": ERROR_INFO_TYPE,
                "reason": reason,
                "domain": ERROR_INFO_DOMAIN,
                "metadata": {str(key): str(value) for key, value in (metadata or {}).items()},
            }
        ]
    body: JsonMap = {
        "error": error,
        "type": "about:blank",
        "title": title,
        "status": int(status),
    }
    if detail:
        body["detail"] = detail
    return body


def to_wire_json(value: Any, *, field: str = "") -> Any:
    """Return a detached ProtoJSON-compatible projection of ``value``."""
    if isinstance(value, Mapping):
        return {str(key): to_wire_json(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [to_wire_json(item) for item in value]
    if field in _TIMESTAMP_FIELDS and _is_finite_number(value):
        return _iso8601_utc(float(value))
    return value


def _is_finite_number(value: object) -> bool:
    """Return whether ``value`` is a finite non-boolean number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _iso8601_utc(value: float) -> str:
    """Render epoch seconds with millisecond UTC precision."""
    try:
        stamp = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "1970-01-01T00:00:00.000Z"
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
