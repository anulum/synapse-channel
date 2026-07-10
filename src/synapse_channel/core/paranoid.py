# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — paranoid-mode runtime policy
"""Paranoid-mode policy checks for local Synapse runtimes.

The ``synapse hub --paranoid`` profile is the production secure preset: it refuses
to start unless the hub is fully hardened — a connect token, a durable event log,
per-message authentication, ACL enforcement with a policy, and native WSS — and it
disables the relaxations that weaken an exposed bind (metrics query tokens and the
insecure off-loopback override). It fails closed for every setting it directly
controls and reports the hardening hooks it still cannot honestly enforce.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from synapse_channel.core.errors import SynapseError

MISSING_PARANOID_HOOKS: tuple[str, ...] = (
    "at-rest encryption (available separately; not enabled by --paranoid)",
    "mutual-TLS client-certificate verification and signed-event trust loading",
    "cryptographic per-agent identity verification (compose --team-secure)",
    "private channels (available separately; not enabled by --paranoid)",
    "end-to-end encrypted channels (available separately; not enabled by --paranoid)",
    "differential-privacy blackboard projections",
    "per-message key rotation and revocation operator workflow",
    "deployment threat-model evidence for exposed bridges",
)
"""Controls that paranoid mode must report as not automatically composed.

Server TLS, ACL enforcement, and HMAC per-message authentication are required by
the profile (see :func:`apply_paranoid_hub_profile`). Several other controls
ship independently but are not enabled by this flag; mutual-TLS client
verification and packaged signed-event trust loading remain unavailable on the
hub CLI. Qualifying the shipped controls here prevents the operator report from
claiming that the underlying feature is absent.
"""


class ParanoidModeError(SynapseError, ValueError):
    """Raised when a runtime cannot satisfy paranoid-mode requirements."""

    code = "paranoid_mode"


@dataclass(frozen=True)
class ParanoidHubReport:
    """Effective paranoid-mode hub posture.

    Attributes
    ----------
    enforced : tuple[str, ...]
        Runtime settings that the CLI checked or normalised before startup.
    missing_hooks : tuple[str, ...]
        Controls the profile does not compose, including separately available
        opt-ins and genuinely unavailable hooks.
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
    if not bool(getattr(args, "require_acl", False)) or not getattr(args, "acl_policy", None):
        raise ParanoidModeError("paranoid mode requires --require-acl with an --acl-policy")
    if not getattr(args, "tls_certfile", None) or not getattr(args, "tls_keyfile", None):
        raise ParanoidModeError(
            "paranoid mode requires native WSS: --tls-certfile and --tls-keyfile"
        )

    args.metrics_query_token_ok = False
    args.insecure_off_loopback = False
    enforced = [
        "hub token required",
        "durable event log required",
        "per-message authentication required",
        "ACL enforcement required",
        "native WSS (TLS) required",
        "metrics query tokens disabled",
        "insecure off-loopback override disabled",
    ]
    if metrics_enabled:
        enforced.append("metrics bearer-token auth required")
    return ParanoidHubReport(
        enforced=tuple(enforced),
    )
