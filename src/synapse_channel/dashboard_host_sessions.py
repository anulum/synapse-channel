# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit host metadata grants
"""Serve read-only host observations independently of dashboard role hints."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path

from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.host_sessions import HostSessionMonitor

HOST_SESSION_PATHS = frozenset({"/host-sessions.json", "/host-sessions-access.json"})


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate host policy key")
        result[key] = value
    return result


def load_host_grants(path: Path) -> dict[str, tuple[bool, bool]]:
    """Load current explicit observer grants from a bounded owner-only JSON file.

    Parameters
    ----------
    path : pathlib.Path
        Policy with version 1 and observers mapping principal IDs to exactly
        paths/context booleans. Presence grants observation; omission revokes it.

    Returns
    -------
    dict
        Principal IDs mapped to path and context disclosure grants.

    Raises
    ------
    ValueError
        When the policy is unavailable, unsafe or malformed.
    """
    raw = read_secret_file(path, flag="--host-sessions-access-file", require_single_link=True)
    doc: object = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(doc, dict) or set(doc) != {"version", "observers"}:
        raise ValueError("host policy needs version and observers")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise ValueError("host policy version must be 1")
    entries = doc["observers"]
    if not isinstance(entries, dict) or len(entries) > 64:
        raise ValueError("host policy allows at most 64 observers")
    grants: dict[str, tuple[bool, bool]] = {}
    for principal, fields in entries.items():
        if not isinstance(principal, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", principal
        ):
            raise ValueError("invalid host principal")
        if not isinstance(fields, dict) or set(fields) != {"paths", "context"}:
            raise ValueError("host grant needs paths and context")
        if type(fields["paths"]) is not bool or type(fields["context"]) is not bool:
            raise ValueError("host grants must be booleans")
        grants[principal] = (fields["paths"], fields["context"])
    return grants


def host_session_response(
    path: str,
    authorization: str | None,
    policy: DashboardAccessPolicy,
    grants_file: Path | None,
    monitor: HostSessionMonitor,
) -> AccessHttpDecision:
    """Authorise before observation and recheck grants before serialisation.

    Parameters
    ----------
    path : str
        One of HOST_SESSION_PATHS, dispatched by the dashboard router.
    authorization : str or None
        Incoming bearer header. It is never included in observation data.
    policy : DashboardAccessPolicy
        Current credential resolver; read capability alone is insufficient.
    grants_file : pathlib.Path or None
        Explicit principal disclosure policy; none disables both routes.
    monitor : HostSessionMonitor
        Dashboard-owned shared collector, invoked only after authorisation.

    Returns
    -------
    AccessHttpDecision
        JSON observation or grant descriptor on success. Disabled, unauthenticated,
        ungranted and busy requests return 404, 401, 403 and 503 respectively.
        Invalid or revoked policy refuses access; no stale data fallback exists.
        The dashboard response writer adds Cache-Control: no-store.
    """
    headers = (("Vary", "Authorization"),)
    if grants_file is None:
        return AccessHttpDecision(
            HTTPStatus.NOT_FOUND, b"host monitor not configured", headers=headers
        )
    principal = policy.resolve_credential(authorization)
    if principal is None or not principal.capabilities.read:
        return AccessHttpDecision(
            HTTPStatus.UNAUTHORIZED, b"bearer required", authenticate=True, headers=headers
        )
    try:
        grants = load_host_grants(grants_file)
        grant = grants.get(principal.principal_id)
        if grant is None:
            return AccessHttpDecision(
                HTTPStatus.FORBIDDEN, b"host observation not granted", headers=headers
            )
        paths, context = grant
        if path == "/host-sessions-access.json":
            body = json.dumps(
                {"version": 1, "observe": True, "paths": paths, "context": context}
            ).encode()
        else:
            observation = monitor.snapshot(paths=paths, context=context)
            if load_host_grants(grants_file).get(principal.principal_id) != grant:
                return AccessHttpDecision(
                    HTTPStatus.FORBIDDEN, b"host grant changed", headers=headers
                )
            body = observation.to_json()
            if len(body) > 1048576:
                return AccessHttpDecision(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    b"host observation exceeds limit",
                    headers=headers,
                )
    except TimeoutError:
        return AccessHttpDecision(
            HTTPStatus.SERVICE_UNAVAILABLE, b"host observation busy", headers=headers
        )
    except (OSError, ValueError):
        return AccessHttpDecision(HTTPStatus.FORBIDDEN, b"host policy unavailable", headers=headers)
    return AccessHttpDecision(HTTPStatus.OK, body, principal, headers=headers)
