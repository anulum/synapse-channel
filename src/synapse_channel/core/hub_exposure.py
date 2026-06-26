# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind exposure checks for the routing hub
"""Bind exposure checks for the routing hub."""

from __future__ import annotations

import logging
from typing import Any

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Bind hosts treated as loopback-only, where running without a token is fine."""


def is_loopback_host(host: str) -> bool:
    """Return whether ``host`` binds only the loopback interface."""
    return host.strip().lower() in LOOPBACK_HOSTS


class InsecureBindError(RuntimeError):
    """Raised when a hub would bind off-loopback without an authenticating guard.

    By default the hub refuses to bind a non-loopback interface unless a token
    authenticator is configured (and, when metrics are served, a metrics token),
    so a coordination bus is never accidentally exposed unauthenticated to the
    network. An operator who accepts the risk passes ``insecure_off_loopback``
    (CLI: ``--insecure-off-loopback``) to downgrade the refusal to a warning.
    """


def exposure_problems(
    host: str,
    *,
    authenticator: Any | None,
    enable_metrics: bool,
    metrics_token: str | None,
) -> list[str]:
    """Return the exposure problems for binding on ``host``."""
    if is_loopback_host(host):
        return []
    problems: list[str] = []
    if authenticator is None:
        problems.append(
            f"bound to non-loopback host {host!r} with no token; set an "
            "authenticator (synapse hub --token ...) before exposing it"
        )
    if enable_metrics and metrics_token is None:
        problems.append(
            f"metrics enabled on non-loopback host {host!r} with no "
            "--metrics-token; /metrics and /health would be unauthenticated"
        )
    return problems


def guard_exposure(
    host: str,
    *,
    authenticator: Any | None,
    enable_metrics: bool,
    metrics_token: str | None,
    insecure_off_loopback: bool,
    logger: logging.Logger,
) -> None:
    """Refuse, or warn before, binding an exposed host without matching guards."""
    problems = exposure_problems(
        host,
        authenticator=authenticator,
        enable_metrics=enable_metrics,
        metrics_token=metrics_token,
    )
    if not problems:
        return
    if insecure_off_loopback:
        for problem in problems:
            logger.warning("Synapse Hub %s.", problem)
        return
    joined = "; ".join(problems)
    raise InsecureBindError(
        f"Refusing to bind: Synapse Hub {joined}. Configure a token "
        "(and --metrics-token when metrics are on), or pass "
        "--insecure-off-loopback to bind anyway (not recommended)."
    )
