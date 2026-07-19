# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — proportionate shared-secret connect authentication
"""Proportionate connect authentication for the Synapse hub.

The bus is local-first and single-owner: by default the hub binds to loopback
and runs with no authentication, which is the right posture for one operator on
one machine. This module adds the *proportionate* next step for when that is no
longer enough — a worker spawned with tool-use, or a hub bound off-loopback: a
shared-secret token a connecting agent must present, optionally bound to a set
of permitted agent names.

This is deliberately not a cryptographic identity system. There is no key
exchange, no signatures, and no per-message authentication — a single secret
gates the connection, compared in constant time (:func:`hmac.compare_digest`) so
a wrong token leaks nothing through comparison timing. When no token is
configured the hub stays open; configure one only when the deployment warrants
it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping

_QUOTA_PRINCIPAL_DOMAIN = b"synapse-channel:claim-quota-principal:v1\x00"
"""Domain prefix for non-secret, stable connect-token quota buckets."""


class TokenAuthenticator:
    """Validates a shared-secret token, optionally bound to agent names.

    Parameters
    ----------
    tokens : Mapping[str, Iterable[str]] or Iterable[str]
        Either a mapping of ``token -> permitted agent names`` (an empty name
        set permits any agent), or a plain iterable of tokens that each permit
        any agent. Empty-string tokens are dropped.

    Notes
    -----
    An authenticator constructed with no usable tokens denies every connection;
    pass ``None`` to :class:`~synapse_channel.core.hub.SynapseHub` to leave the hub
    open instead.
    """

    def __init__(self, tokens: Mapping[str, Iterable[str]] | Iterable[str]) -> None:
        normalised: dict[str, frozenset[str]] = {}
        if isinstance(tokens, Mapping):
            for token, agents in tokens.items():
                normalised[str(token)] = frozenset(str(agent) for agent in agents)
        else:
            for token in tokens:
                normalised[str(token)] = frozenset()
        self._tokens = {token: agents for token, agents in normalised.items() if token}

    @property
    def is_empty(self) -> bool:
        """Whether no usable token is configured (so every connection is denied)."""
        return not self._tokens

    def authenticate(self, token: str, agent: str) -> tuple[bool, str]:
        """Check a presented token for a connecting agent.

        Parameters
        ----------
        token : str
            The secret the agent presented; an empty value is always refused.
        agent : str
            The agent name the connection claims, checked against any name
            binding on the matched token.

        Returns
        -------
        tuple[bool, str]
            ``(True, message)`` when the token is valid and permits the agent,
            otherwise ``(False, reason)``.
        """
        ok, reason, _principal = self.authenticate_with_principal(token, agent)
        return ok, reason

    def authenticate_with_principal(self, token: str, agent: str) -> tuple[bool, str, str | None]:
        """Authenticate and return the stable quota bucket for the credential.

        The principal is a domain-separated SHA-256 fingerprint of the matched
        connect token. It is deliberately not the asserted agent name: every name
        admitted by one credential shares one claim budget, so reconnecting under
        aliases cannot multiply the quota. The raw token is never returned or
        logged. Callers must keep the fingerprint internal; it is persisted only in
        private claim snapshots so a restart preserves the same budget.

        Returns
        -------
        tuple[bool, str, str or None]
            Authentication verdict, human-readable reason, and the stable quota
            principal on success. Refusals always carry ``None``.
        """
        candidate = str(token)
        if not candidate:
            return False, "Authentication token required.", None
        probe = candidate.encode("utf-8")
        for known, allowed in self._tokens.items():
            if hmac.compare_digest(known.encode("utf-8"), probe):
                if allowed and agent not in allowed:
                    return False, f"Token is not authorised for agent '{agent}'.", None
                digest = hashlib.sha256(_QUOTA_PRINCIPAL_DOMAIN + probe).hexdigest()
                return True, "Authenticated.", f"auth-token:{digest}"
        return False, "Invalid authentication token.", None
