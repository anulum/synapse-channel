# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation proxy path policy
"""Classify federation proxy paths for certificate-pinned deployments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

FederationPathMode = Literal[
    "direct-mtls",
    "tls-passthrough",
    "tailnet",
    "tls-terminating-proxy",
]
"""Supported federation transport path declarations."""

FederationPathStatus = Literal["pass", "fail"]
"""Machine-readable outcome for one federation transport path."""

SUPPORTED_FEDERATION_PATH_MODES: frozenset[str] = frozenset(
    {
        "direct-mtls",
        "tls-passthrough",
        "tailnet",
        "tls-terminating-proxy",
    }
)
"""Modes accepted by the federation path classifier."""


@dataclass(frozen=True, slots=True)
class FederationProxyVerdict:
    """Verdict for one declared federation transport path.

    Attributes
    ----------
    status : {"pass", "fail"}
        Whether the path preserves the certificate-pinning trust boundary.
    detail : str
        Operator-facing finding text.
    remedy : str
        Operator-facing remediation text for failures.
    """

    status: FederationPathStatus
    detail: str
    remedy: str = ""


def classify_federation_proxy_path(mode: FederationPathMode) -> FederationProxyVerdict:
    """Return the certificate-pinning verdict for a federation transport mode.

    Parameters
    ----------
    mode : FederationPathMode
        Declared peer path mode.

    Returns
    -------
    FederationProxyVerdict
        Policy result for the path.
    """
    if mode == "direct-mtls":
        return FederationProxyVerdict(
            status="pass",
            detail=(
                "direct mTLS/WSS path preserves the peer certificate pin and client "
                "certificate boundary"
            ),
        )
    if mode == "tls-passthrough":
        return FederationProxyVerdict(
            status="pass",
            detail=(
                "TLS passthrough proxy preserves the hub TLS certificate and lets the "
                "hub verify client certificates"
            ),
        )
    if mode == "tailnet":
        return FederationProxyVerdict(
            status="pass",
            detail=(
                "tailnet path keeps the hub off the public internet; pair it with the "
                "normal token and certificate-pin ceremony when WSS is used"
            ),
        )
    return FederationProxyVerdict(
        status="fail",
        detail=(
            "TLS-terminating reverse proxy presents the proxy certificate to the peer, "
            "so hub certificate pins and socket-level client certificates do not reach "
            "the hub"
        ),
        remedy=(
            "use direct mTLS/WSS, TCP/TLS passthrough, or a tailnet path; do not treat "
            "plain TLS termination as the same federation trust boundary"
        ),
    )


def normalise_federation_path_mode(raw: str) -> FederationPathMode | None:
    """Normalise an operator-supplied federation path mode.

    Parameters
    ----------
    raw : str
        Raw mode from CLI or configuration.

    Returns
    -------
    FederationPathMode or None
        Supported mode, or ``None`` when the value is unknown.
    """
    candidate = raw.strip().lower().replace("_", "-")
    if candidate == "direct":
        candidate = "direct-mtls"
    elif candidate in {"passthrough", "tls-pass-through"}:
        candidate = "tls-passthrough"
    elif candidate in {"terminating-proxy", "reverse-proxy", "caddy"}:
        candidate = "tls-terminating-proxy"
    if candidate not in SUPPORTED_FEDERATION_PATH_MODES:
        return None
    return cast(FederationPathMode, candidate)
