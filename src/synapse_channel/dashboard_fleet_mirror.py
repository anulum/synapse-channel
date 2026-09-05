# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicitly granted Fleet mirror HTTP reads
"""Keep Fleet metadata disclosure separate from ordinary dashboard access."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path

from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.fleet_mirror_contract import MAX_MIRROR_BYTES, MirrorVersionError, parse_mirror

FLEET_MIRROR_PATHS = frozenset({"/fleet-observed.json", "/fleet-observed-access.json"})


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate grant key")
        result[key] = value
    return result


def load_mirror_grants(path: Path) -> set[str]:
    """Read a bounded owner-only version-one observer list.

    Parameters
    ----------
    path : pathlib.Path
        JSON containing version=1 and observers, a list of principal IDs.

    Returns
    -------
    set of str
        Explicit observers; omission revokes permission.

    Raises
    ------
    ValueError
        If the file or policy is unavailable or invalid.
    """
    doc = json.loads(
        read_secret_file(path, flag="Fleet grants", require_single_link=True),
        object_pairs_hook=_unique,
    )
    if not isinstance(doc, dict) or set(doc) != {"version", "observers"}:
        raise ValueError("invalid mirror grants")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise ValueError("invalid grant version")
    entries = doc["observers"]
    if not isinstance(entries, list) or len(entries) > 64:
        raise ValueError("invalid observers")
    if any(
        not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", x)
        for x in entries
    ):
        raise ValueError("invalid observer")
    if len(set(entries)) != len(entries):
        raise ValueError("duplicate observer")
    return set(entries)


def fleet_mirror_response(
    path: str,
    authorization: str | None,
    policy: DashboardAccessPolicy,
    export_file: Path | None,
    grants_file: Path | None,
) -> AccessHttpDecision:
    """Authorise before reading a bounded mirror and recheck before disclosure.

    Parameters
    ----------
    path : str
        One of FLEET_MIRROR_PATHS.
    authorization : str or None
        Request bearer; never returned.
    policy : DashboardAccessPolicy
        Current dashboard credentials.
    export_file, grants_file : pathlib.Path or None
        Both explicit paths are required; absence disables this feed.

    Returns
    -------
    AccessHttpDecision
        200 observation/descriptor, 404 disabled, 401 unauthenticated,
        403 ungranted, 503 unavailable/invalid, or 409 incompatible version.
        Bodies never include local paths or data on failure.
    """
    headers = (("Vary", "Authorization"),)

    def reply(status: HTTPStatus, body: bytes) -> AccessHttpDecision:
        return AccessHttpDecision(
            status, body, headers=headers, authenticate=status == HTTPStatus.UNAUTHORIZED
        )

    if export_file is None or grants_file is None:
        return reply(HTTPStatus.NOT_FOUND, b"not configured")
    principal = policy.resolve_credential(authorization)
    if principal is None or not principal.capabilities.read:
        return reply(HTTPStatus.UNAUTHORIZED, b"bearer required")
    try:
        if principal.principal_id not in load_mirror_grants(grants_file):
            return reply(HTTPStatus.FORBIDDEN, b"mirror observation not granted")
    except (ValueError, OSError):
        return reply(HTTPStatus.FORBIDDEN, b"mirror grants unavailable")
    if path == "/fleet-observed-access.json":
        body = b'{"version":1,"observe":true,"advisory":true}'
    else:
        try:
            raw = read_secret_file(
                export_file, flag="Fleet export", require_single_link=True, limit=MAX_MIRROR_BYTES
            )
        except (ValueError, OSError):
            return reply(HTTPStatus.SERVICE_UNAVAILABLE, b"mirror unavailable")
        try:
            body = json.dumps(parse_mirror(raw), allow_nan=False).encode()
        except MirrorVersionError:
            return reply(HTTPStatus.CONFLICT, b"mirror incompatible")
        except ValueError:
            return reply(HTTPStatus.SERVICE_UNAVAILABLE, b"mirror invalid")
    try:
        current = policy.resolve_credential(authorization)
        if current != principal or principal.principal_id not in load_mirror_grants(grants_file):
            return reply(HTTPStatus.FORBIDDEN, b"mirror grant changed")
    except (ValueError, OSError):
        return reply(HTTPStatus.FORBIDDEN, b"mirror grants unavailable")
    return AccessHttpDecision(HTTPStatus.OK, body, principal, headers=headers)
