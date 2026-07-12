# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable dashboard principal and capability policy
"""Model browser principals without confusing presentation with authority."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Literal

DashboardRole = Literal["viewer", "operator", "admin"]
DashboardCapability = Literal["read", "message_send", "task_declare", "task_update"]


@dataclass(frozen=True)
class DashboardCapabilities:
    """Exact dashboard routes one authenticated principal may use."""

    read: bool
    message_send: bool
    task_declare: bool
    task_update: bool

    def allows(self, capability: DashboardCapability) -> bool:
        """Return the named capability without accepting arbitrary attributes."""
        return {
            "read": self.read,
            "message_send": self.message_send,
            "task_declare": self.task_declare,
            "task_update": self.task_update,
        }[capability]

    def as_dict(self) -> dict[str, bool]:
        """Return the stable access-descriptor representation."""
        return {
            "read": self.read,
            "message_send": self.message_send,
            "task_declare": self.task_declare,
            "task_update": self.task_update,
        }


@dataclass(frozen=True)
class DashboardPrincipal:
    """One server-authored browser identity and its current UI role."""

    principal_id: str
    role: DashboardRole
    capabilities: DashboardCapabilities
    operator_name: str | None = None


@dataclass(frozen=True)
class DashboardCredential:
    """A principal bound to secret bearer bytes hidden from representations."""

    principal: DashboardPrincipal
    token: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Refuse an unusable credential even when constructed outside the loader."""
        if not isinstance(self.token, bytes) or not self.token:
            raise ValueError("dashboard credential token must be non-empty bytes")


@dataclass(frozen=True)
class DashboardAccessPolicy:
    """Immutable request policy resolved once at dashboard startup."""

    credentials: tuple[DashboardCredential, ...]
    open_principal: DashboardPrincipal | None
    operator_armed: bool
    compatibility: bool = False

    @property
    def reads_gated(self) -> bool:
        """Return whether every read requires a recognised bearer."""
        return self.open_principal is None

    def resolve_credential(self, authorization: str | None) -> DashboardPrincipal | None:
        """Resolve a bearer against every configured token without early exit."""
        presented = _bearer_bytes(authorization)
        candidate = b"" if presented is None else presented
        matched: DashboardPrincipal | None = None
        for credential in self.credentials:
            if hmac.compare_digest(candidate, credential.token):
                matched = credential.principal
        return None if presented is None else matched

    def resolve_read(self, authorization: str | None) -> DashboardPrincipal | None:
        """Resolve a read principal, falling back only on an explicit open role."""
        return self.resolve_credential(authorization) or self.open_principal


def capabilities_for_role(
    role: DashboardRole,
    *,
    operator_armed: bool,
) -> DashboardCapabilities:
    """Return current capabilities; admin invents no unshipped mutation."""
    writes = role in {"operator", "admin"} and operator_armed
    return DashboardCapabilities(
        read=True,
        message_send=writes,
        task_declare=writes,
        task_update=writes,
    )


def compatibility_access_policy(
    *,
    dashboard_token: str | None,
    token_protects_reads: bool,
    operator_armed: bool,
    operator_name: str,
) -> DashboardAccessPolicy:
    """Translate the legacy one-token posture without changing its read boundary."""
    viewer = DashboardPrincipal(
        "local-viewer",
        "viewer",
        capabilities_for_role("viewer", operator_armed=operator_armed),
    )
    credentials: tuple[DashboardCredential, ...] = ()
    if dashboard_token is not None:
        if not dashboard_token:
            raise ValueError("dashboard token must not be empty")
        role: DashboardRole = "operator" if operator_armed else "viewer"
        principal = DashboardPrincipal(
            "compatibility",
            role,
            capabilities_for_role(role, operator_armed=operator_armed),
            operator_name if role == "operator" else None,
        )
        credentials = (DashboardCredential(principal, dashboard_token.encode("utf-8")),)
    elif token_protects_reads or operator_armed:
        raise ValueError("dashboard access posture requires a bearer token")
    return DashboardAccessPolicy(
        credentials=credentials,
        open_principal=None if token_protects_reads else viewer,
        operator_armed=operator_armed,
        compatibility=True,
    )


def _bearer_bytes(authorization: str | None) -> bytes | None:
    """Return exact bearer bytes from one well-formed Authorization value."""
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ")
    if not token or token != token.strip() or any(character.isspace() for character in token):
        return None
    return token.encode("utf-8")
