# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — paranoid-mode runtime policy
"""Paranoid-mode policy checks for local Synapse runtimes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

MISSING_PARANOID_HOOKS: tuple[str, ...] = (
    "at-rest encryption",
    "signed events and mTLS enforcement",
    "per-agent identity and ACL enforcement",
    "private channels",
    "end-to-end encrypted channels",
    "differential-privacy blackboard projections",
    "per-message key rotation and revocation operator workflow",
    "deployment threat-model evidence for exposed bridges",
)
"""Runtime hooks that paranoid mode must report as unavailable today."""


class ParanoidModeError(ValueError):
    """Raised when a runtime cannot satisfy paranoid-mode requirements."""


@dataclass(frozen=True)
class ParanoidHubReport:
    """Effective paranoid-mode hub posture.

    Attributes
    ----------
    enforced : tuple[str, ...]
        Runtime settings that the CLI checked or normalised before startup.
    missing_hooks : tuple[str, ...]
        Future security hooks that are still unavailable and must not be implied.
    """

    enforced: tuple[str, ...]
    missing_hooks: tuple[str, ...] = MISSING_PARANOID_HOOKS

    def stderr_lines(self) -> tuple[str, ...]:
        """Return human-readable report lines for the operator."""
        enforced = ", ".join(self.enforced)
        missing = ", ".join(self.missing_hooks)
        return (
            f"paranoid mode enforced: {enforced}",
            f"paranoid mode missing hooks: {missing}",
        )


def apply_paranoid_hub_profile(args: argparse.Namespace) -> ParanoidHubReport | None:
    """Validate and normalise ``synapse hub --paranoid`` settings.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments. The function mutates only relaxed options that
        paranoid mode must disable before the hub is constructed.

    Returns
    -------
    ParanoidHubReport or None
        Report for operator output when paranoid mode is enabled; ``None`` when
        the switch is off.

    Raises
    ------
    ParanoidModeError
        If a required setting is absent.
    """
    if not bool(getattr(args, "paranoid", False)):
        return None
    if not getattr(args, "token", None):
        raise ParanoidModeError("paranoid mode requires --token or --token-file")
    if not getattr(args, "db", None):
        raise ParanoidModeError("paranoid mode requires --db for durable event-log replay")
    metrics_enabled = bool(getattr(args, "metrics", False))
    if metrics_enabled and not getattr(args, "metrics_token", None):
        raise ParanoidModeError("paranoid mode requires --metrics-token when --metrics is enabled")
    if not getattr(args, "message_auth_key", None):
        raise ParanoidModeError("paranoid mode requires --message-auth-key")
    if not bool(getattr(args, "require_message_auth", False)):
        raise ParanoidModeError("paranoid mode requires --require-message-auth")

    args.metrics_query_token_ok = False
    args.insecure_off_loopback = False
    enforced = [
        "hub token required",
        "durable event log required",
        "per-message authentication required",
        "metrics query tokens disabled",
        "insecure off-loopback override disabled",
    ]
    if metrics_enabled:
        enforced.append("metrics bearer-token auth required")
    return ParanoidHubReport(
        enforced=tuple(enforced),
    )
