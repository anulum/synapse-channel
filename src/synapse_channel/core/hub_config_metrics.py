# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub metrics and liveness configuration records
"""HTTP metrics and recipient-liveness configuration records for SynapseHub."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.agent_liveness import (
    DEFAULT_RECIPIENT_LIVENESS_WINDOW,
    DEFAULT_WAITER_LIVENESS_WINDOW,
    DEFAULT_WARN_STALE_RECIPIENTS,
)
from synapse_channel.core.auth import TokenAuthenticator

__all__ = ["HubLiveness", "HubMetricsConfig"]


@dataclass(frozen=True, kw_only=True)
class HubMetricsConfig:
    """The optional HTTP ``/metrics`` and ``/health`` endpoint and its token or token set.

    ``metrics_token_set`` (hub keyword of the same name) is an authenticator for the two
    routes beside, or instead of, ``metrics_token``: normally a
    :class:`~synapse_channel.core.token_set.TokenSetAuthenticator` read from an owner-only
    token-set file (``--metrics-token-set``). The file is re-read when it changes, so the
    metrics bearer rotates without a restart; entries carry validity windows, and an entry
    that names agents admits no metrics request. With a journal, each applied or rejected
    set is an audit row.
    """

    enable_metrics: bool = False
    metrics_token: str | None = None
    metrics_token_set: TokenAuthenticator | None = None
    metrics_query_token_ok: bool = False
    allowed_origins: tuple[str, ...] = ()
    advertised_host: str | None = None


@dataclass(frozen=True, kw_only=True)
class HubLiveness:
    """The stale-recipient policy that tells a present agent from a deaf one.

    On by default: a directed message to a recipient that is present but has no
    proof of liveness — no ``-rx`` waiter sidecar whose keepalive is fresh within
    ``waiter_liveness_window`` seconds, and no genuine reaction within
    ``recipient_liveness_window`` seconds — draws a private warning, cannot count
    as a positive directed delivery, and is marked distinctly by ``/who``.
    Operators can opt out explicitly for the legacy socket-presence behaviour.
    """

    warn_stale_recipients: bool = DEFAULT_WARN_STALE_RECIPIENTS
    recipient_liveness_window: float = DEFAULT_RECIPIENT_LIVENESS_WINDOW
    waiter_liveness_window: float = DEFAULT_WAITER_LIVENESS_WINDOW
