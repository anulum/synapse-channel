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

from synapse_channel.core.errors import SynapseError

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Bind hosts treated as loopback-only, where running without a token is fine."""


def is_loopback_host(host: str) -> bool:
    """Return whether ``host`` binds only the loopback interface."""
    return host.strip().lower() in LOOPBACK_HOSTS


class InsecureBindError(SynapseError, RuntimeError):
    """Raised when a hub would bind off-loopback without an authenticating guard.

    By default the hub refuses to bind a non-loopback interface unless a token
    authenticator is configured (and, when metrics are served, a metrics token),
    so a coordination bus is never accidentally exposed unauthenticated to the
    network. From the 1.0 posture the refusal also covers a shared token presented
    over plaintext ``ws://`` off loopback: the token and every coordination frame
    would be readable on the network path, so the bind requires native TLS (or a
    ``wss://`` proxy front). An operator who accepts either risk passes
    ``insecure_off_loopback`` (CLI: ``--insecure-off-loopback``) to downgrade the
    refusal to a warning.
    """

    code = "insecure_bind"


def plaintext_token_problems(
    host: str,
    *,
    authenticator: Any | None,
    tls_active: bool,
) -> list[str]:
    """Return the plaintext-token exposure problems for binding on ``host``.

    A shared token presented over plaintext ``ws://`` off loopback puts the token
    and every coordination frame on the wire in the clear. From the 1.0 posture
    this is a refusal, not a mere advisory: the bind must terminate TLS natively
    (``--tls-certfile``/``--tls-keyfile``) or sit behind a ``wss://`` proxy, or the
    operator must accept the risk explicitly with ``--insecure-off-loopback``.
    Loopback binds, TLS-terminated binds, and token-less binds are handled
    elsewhere (the token-less case is the separate no-token refusal), so this
    check fires only for authenticator + off-loopback + no TLS.
    """
    if is_loopback_host(host) or tls_active or authenticator is None:
        return []
    return [
        f"authenticates with a shared token on non-loopback host {host!r} over "
        "plaintext ws://; the token and all coordination traffic are readable "
        "on the network path — terminate TLS natively (--tls-certfile and "
        "--tls-keyfile) or front the hub with a wss:// proxy; --paranoid makes "
        "native WSS mandatory"
    ]


def exposure_problems(
    host: str,
    *,
    authenticator: Any | None,
    enable_metrics: bool,
    metrics_token: str | None,
    metrics_query_token_ok: bool = False,
) -> list[str]:
    """Return the transport-independent exposure problems for binding on ``host``.

    These are the problems that do not depend on whether the bind terminates TLS:
    a token-less off-loopback bind, metrics served without a metrics token, and a
    metrics query-string token that would leak into URL logs. The transport-
    dependent plaintext-token refusal (a token over plaintext ``ws://`` off
    loopback) is evaluated separately at bind time by :func:`guard_exposure`, which
    knows ``tls_active``; a token-set hub can therefore report no problems here yet
    still be refused by the guard when it would bind off loopback without TLS.
    """
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
    if enable_metrics and metrics_query_token_ok:
        problems.append(
            f"metrics query-string token accepted on non-loopback host {host!r}; a "
            "?token= value leaks into proxy access logs, browser history, and shell "
            "history — drop --metrics-query-token-ok and pass the token in the "
            "Authorization header, or bind loopback where it is a local-only debug aid"
        )
    return problems


def guard_exposure(
    host: str,
    *,
    authenticator: Any | None,
    enable_metrics: bool,
    metrics_token: str | None,
    metrics_query_token_ok: bool = False,
    insecure_off_loopback: bool,
    tls_active: bool = False,
    logger: logging.Logger,
) -> None:
    """Refuse, or warn before, binding an exposed host without matching guards.

    The transport-independent problems from :func:`exposure_problems` and the
    transport-dependent plaintext-token refusal from
    :func:`plaintext_token_problems` are evaluated together: off loopback without
    TLS a shared token rides plaintext ``ws://``, so from the 1.0 posture that is a
    refusal alongside the token-less and metrics cases. ``insecure_off_loopback``
    downgrades every one of them to a warning; ``tls_active=True`` clears the
    plaintext-token problem entirely.
    """
    problems = exposure_problems(
        host,
        authenticator=authenticator,
        enable_metrics=enable_metrics,
        metrics_token=metrics_token,
        metrics_query_token_ok=metrics_query_token_ok,
    )
    problems += plaintext_token_problems(host, authenticator=authenticator, tls_active=tls_active)
    if not problems:
        return
    if insecure_off_loopback:
        for problem in problems:
            logger.warning("Synapse Hub %s.", problem)
        return
    joined = "; ".join(problems)
    raise InsecureBindError(
        f"Refusing to bind: Synapse Hub {joined}. Resolve the problem above "
        "(configure a token and --metrics-token when metrics are on, or terminate "
        "TLS off loopback), or pass --insecure-off-loopback to bind anyway "
        "(not recommended)."
    )
