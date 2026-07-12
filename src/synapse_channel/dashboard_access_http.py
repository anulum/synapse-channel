# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure dashboard access descriptor and HTTP decisions
"""Keep capability hints and authoritative route checks in one small seam."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final, cast

from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardCapability,
    DashboardPrincipal,
)

DASHBOARD_ACCESS_PATH: Final = "/dashboard-access.json"
MESSAGE_PATH: Final = "/message"
TASK_PATH: Final = "/task"
TASK_UPDATE_PATH: Final = "/task/update"
TRUST_BOUNDARY: Final = "presentation hints only; HTTP and hub policy enforce writes"
_ROUTE_CAPABILITY: Final[dict[str, DashboardCapability]] = {
    MESSAGE_PATH: "message_send",
    TASK_PATH: "task_declare",
    TASK_UPDATE_PATH: "task_update",
}


@dataclass(frozen=True)
class AccessHttpDecision:
    """One complete access response or an allowed principal."""

    status: HTTPStatus | None
    body: bytes
    principal: DashboardPrincipal | None = None
    authenticate: bool = False
    headers: tuple[tuple[str, str], ...] = ()

    @property
    def allowed(self) -> bool:
        """Return whether request processing may continue."""
        return self.status is None and self.principal is not None


def read_decision(
    policy: DashboardAccessPolicy,
    authorization: str | None,
) -> AccessHttpDecision:
    """Resolve a read, returning a generic bearer challenge on failure."""
    principal = policy.resolve_read(authorization)
    if principal is None or not principal.capabilities.read:
        return _denied(HTTPStatus.UNAUTHORIZED, authenticate=True)
    return AccessHttpDecision(None, b"", principal)


def access_descriptor_decision(
    policy: DashboardAccessPolicy,
    authorization: str | None,
) -> AccessHttpDecision:
    """Return the authenticated principal's token-free capability document."""
    decision = read_decision(policy, authorization)
    if not decision.allowed:
        return AccessHttpDecision(
            decision.status,
            decision.body,
            authenticate=decision.authenticate,
            headers=(("Vary", "Authorization"),),
        )
    principal = cast(DashboardPrincipal, decision.principal)
    payload = {
        "version": 1,
        "principal": principal.principal_id,
        "role": principal.role,
        "capabilities": principal.capabilities.as_dict(),
        "operator_armed": policy.operator_armed,
        "trust_boundary": TRUST_BOUNDARY,
    }
    return AccessHttpDecision(
        HTTPStatus.OK,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ),
        principal,
        headers=(("Vary", "Authorization"),),
    )


def write_decision(
    policy: DashboardAccessPolicy,
    authorization: str | None,
    route: str,
) -> AccessHttpDecision:
    """Re-resolve one write route; browser capabilities never authorize it."""
    if not policy.operator_armed:
        return _denied(HTTPStatus.NOT_FOUND)
    principal = policy.resolve_credential(authorization)
    if principal is None:
        return _denied(HTTPStatus.UNAUTHORIZED, authenticate=True)
    capability = _ROUTE_CAPABILITY.get(route)
    if capability is None:
        return _denied(HTTPStatus.NOT_FOUND)
    if not principal.capabilities.allows(capability) or principal.operator_name is None:
        return _denied(HTTPStatus.FORBIDDEN)
    return AccessHttpDecision(None, b"", principal)


def _denied(status: HTTPStatus, *, authenticate: bool = False) -> AccessHttpDecision:
    messages = {
        HTTPStatus.UNAUTHORIZED: b"dashboard authorization required\n",
        HTTPStatus.FORBIDDEN: b"dashboard capability denied\n",
        HTTPStatus.NOT_FOUND: b"not found\n",
    }
    return AccessHttpDecision(status, messages[status], authenticate=authenticate)
