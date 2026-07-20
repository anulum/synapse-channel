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

from synapse_channel.a2a import JsonMap
from synapse_channel.core.errors import error_code
from synapse_channel.core.http_authority import (
    endpoint_authorities as endpoint_authorities,
)
from synapse_channel.core.http_authority import (
    normalise_authority as normalise_authority,
)
from synapse_channel.core.http_authority import (
    normalise_origin as normalise_origin,
)

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
