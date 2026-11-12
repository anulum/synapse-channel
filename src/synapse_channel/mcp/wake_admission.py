# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live operator authority for notification delivery
"""Bind a notifier to its original private grant and reload revocation before work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from synapse_channel.mcp.http_config import HttpAuthConfig, HttpConfigError, load_http_auth_config
from synapse_channel.mcp.wake_pointer import WakeLedgerError, WakeScope


class WakeAdmission:
    """Keep notification authority independent of a transient HTTP bearer.

    Parameters
    ----------
    auth_file : pathlib.Path
        Secure current operator grant file, reloaded before each attempt.
    subject, project : str
        Fixed provisioning identity, never selected from a webhook payload.
    config : HttpAuthConfig
        Original validated policy. Changing its grant, target or signing-key
        path requires reprovisioning; rotating key contents at the same secure
        path preserves the immutable delivery identity.
    """

    def __init__(self, auth_file: Path, subject: str, project: str, config: HttpAuthConfig) -> None:
        authority = config.subjects.get(subject)
        grant = authority.projects.get(project) if authority is not None else None
        if (
            authority is None
            or not authority.enabled
            or grant is None
            or grant.wake is None
            or not {"synapse_inbox_page", "synapse_owned_work"} <= grant.tools
        ):
            raise WakeLedgerError("wake delivery is not authorized")
        self.auth_file = auth_file
        self.subject = subject
        self.project = project
        self.grant = grant
        self.endpoint = grant.wake
        self.issuer = config.issuer
        self.resource = config.resource
        payload = grant.model_dump(mode="json")
        payload["tools"] = sorted(grant.tools)
        material = json.dumps(
            [self.issuer, self.resource, subject, project, payload],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.binding = hashlib.sha256(material).hexdigest()

    def check(self) -> Literal["enabled", "disabled", "grant_changed", "unavailable"]:
        """Return current authorization state without exposing any profile values."""
        try:
            config = load_http_auth_config(self.auth_file)
        except HttpConfigError:
            return "unavailable"
        authority = config.subjects.get(self.subject)
        if authority is None or not authority.enabled:
            return "disabled"
        if (
            config.issuer != self.issuer
            or config.resource != self.resource
            or authority.projects.get(self.project) != self.grant
        ):
            return "grant_changed"
        return "enabled"

    def scope(self, hub_id: str) -> WakeScope:
        """Refuse current revocation before binding a ledger to the admitted hub."""
        if self.check() != "enabled":
            raise WakeLedgerError("wake delivery is not authorized")
        return WakeScope(
            self.subject,
            self.project,
            self.grant.seat,
            self.binding,
            hub_id,
            self.auth_file.resolve().name,
        )
