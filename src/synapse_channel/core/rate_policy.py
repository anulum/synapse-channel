# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — auto-enable rate-policy decision for exposed hubs
"""Auto-enable flood-limit decision for exposed hubs (REV-SEC-06).

A hub left with flood limits disabled — the local-first default — is safe on a
single-seat loopback bind, but an off-loopback, token-guarded, or bridge-exposed
hub can be flooded by a peer before any operator notices. ``--secure`` closes this
for operators who opt in, yet the common exposed-but-not-``--secure`` hub still
starts unbounded.

This module answers one question and nothing else: **given the startup posture,
should the hub auto-enable safe bounded per-agent and per-host flood limits and a
per-host connection cap, and with what values?** It is a pure decision — it opens
no socket, mutates no namespace, reads no file, and holds no state. The
hub-startup caller computes the posture, calls :func:`decide_auto_rate_policy`,
and applies the returned :class:`RatePolicyDecision` to its limiter configuration.

The policy is a *fill-the-gap safety net*, deliberately distinct from the
``--secure`` *clamp-and-require* preset:

- **Local-first is preserved.** With no exposure signal (loopback bind, no token,
  no bridge, single seat) nothing is auto-enabled; the hub stays unthrottled
  exactly as today.
- **Only disabled limits are filled.** A limit the operator left at its disabled
  sentinel (rate ``<= 0`` or non-finite; connection cap ``<= 0``) is filled with a
  safe bounded default; a limit the operator set positive is preserved verbatim,
  even when looser than the default. Enforcing a *ceiling* on a loose operator
  value is ``--secure``'s job, not the safety net's — auto-enable never overrides
  an informed operator choice, it only covers an omission.
- **``--secure`` wins.** When the secure preset is active it already normalises
  every limit, so auto-enable stands down and reports the deferral.

The bounded default values are reused from :mod:`synapse_channel.core.secure` so
the fleet has one source of truth for "a safe bounded flood limit".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from synapse_channel.core.secure import (
    SECURE_AGENT_BURST,
    SECURE_AGENT_RATE,
    SECURE_HOST_BURST,
    SECURE_HOST_RATE,
    SECURE_MAX_CONNECTIONS_PER_HOST,
)

#: Hosts treated as a genuine loopback bind. Every other value — a LAN/public
#: address, a hostname, the empty string, or a bind-all sentinel (``0.0.0.0`` /
#: ``::``) — is treated as off-loopback, i.e. exposed. The bias is deliberate:
#: an unrecognised bind fails safe toward *more* protection.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"})


def is_loopback_bind(host: str | None) -> bool:
    """Return whether ``host`` is a genuine loopback bind address.

    Parameters
    ----------
    host : str or None
        The hub's ``--host`` bind value.

    Returns
    -------
    bool
        ``True`` only for recognised loopback forms (``localhost``, the
        ``127.0.0.0/8`` block, or IPv6 ``::1``). A bind-all sentinel
        (``0.0.0.0`` / ``::``), any other address or hostname, an empty string,
        or ``None`` returns ``False`` — an unrecognised bind is treated as
        exposed so the safety net errs toward protection.
    """
    if host is None:
        return False
    normalised = host.strip().lower()
    if not normalised:
        return False
    if normalised in _LOOPBACK_HOSTS:
        return True
    # The whole 127.0.0.0/8 block is loopback; match on the dotted prefix rather
    # than parsing, so a malformed "127.foo" still fails safe toward exposed via
    # the trailing checks below.
    return normalised.startswith("127.") and _is_dotted_ipv4(normalised)


def _is_dotted_ipv4(value: str) -> bool:
    """Return whether ``value`` is four dotted decimal octets (0–255)."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True


@dataclass(frozen=True)
class HubExposurePosture:
    """The startup exposure signals that gate flood auto-enable.

    Attributes
    ----------
    off_loopback_bind : bool
        The hub binds a non-loopback address (reachable beyond this machine).
    token_configured : bool
        A connect token is configured, implying a shared/multi-party hub.
    bridge_exposed : bool
        An A2A or MCP bridge surface is knowingly exposed.
    multi_seat : bool
        More than one seat is expected on the hub.
    """

    off_loopback_bind: bool = False
    token_configured: bool = False
    bridge_exposed: bool = False
    multi_seat: bool = False

    @property
    def triggers(self) -> tuple[str, ...]:
        """Return the names of the exposure signals that are set, in a stable order."""
        names = (
            ("off-loopback bind", self.off_loopback_bind),
            ("connect token configured", self.token_configured),
            ("A2A/MCP bridge exposed", self.bridge_exposed),
            ("multi-seat", self.multi_seat),
        )
        return tuple(name for name, active in names if active)

    @property
    def is_exposed(self) -> bool:
        """Return whether any exposure signal is set."""
        return bool(self.triggers)


@dataclass(frozen=True)
class RateLimits:
    """A hub's flood-limit values.

    A rate ``<= 0`` (or non-finite) and a connection cap ``<= 0`` are the disabled
    sentinels the hub already uses; a burst is only meaningful beside an active
    rate.
    """

    agent_rate: float = 0.0
    agent_burst: float = 0.0
    host_rate: float = 0.0
    host_burst: float = 0.0
    max_connections_per_host: int = 0


@dataclass(frozen=True)
class RatePolicyDecision:
    """The outcome of the auto-enable decision.

    Attributes
    ----------
    auto_enabled : bool
        Whether at least one previously disabled limit was auto-filled.
    triggers : tuple of str
        The exposure signals that made the posture exposed (empty when safe).
    limits : RateLimits
        The effective limits to apply: auto-filled where the operator left a
        limit disabled in an exposed posture, operator values preserved otherwise.
    filled : tuple of str
        The names of the limits this decision auto-filled.
    report_lines : tuple of str
        Human-readable lines for the operator explaining the decision.
    """

    auto_enabled: bool
    triggers: tuple[str, ...]
    limits: RateLimits
    filled: tuple[str, ...]
    report_lines: tuple[str, ...]


def _rate_disabled(rate: float) -> bool:
    """Return whether ``rate`` is a disabled sentinel or a non-finite bypass.

    ``nan`` compares false against every ceiling downstream and would construct no
    limiter at all, so it is treated as disabled and filled — the same fail-closed
    reading the secure preset applies.
    """
    return not math.isfinite(rate) or rate <= 0.0


def decide_auto_rate_policy(
    posture: HubExposurePosture,
    operator_limits: RateLimits,
    *,
    secure_mode: bool = False,
) -> RatePolicyDecision:
    """Decide whether to auto-enable bounded flood limits for the given posture.

    Parameters
    ----------
    posture : HubExposurePosture
        The startup exposure signals.
    operator_limits : RateLimits
        The limits the operator supplied (disabled sentinels included).
    secure_mode : bool, optional
        Whether ``--secure`` is active. When ``True`` the secure preset already
        normalises every limit, so this decision stands down.

    Returns
    -------
    RatePolicyDecision
        The effective limits and an explanation. When the posture is safe, or the
        secure preset is active, the operator limits are returned unchanged with
        ``auto_enabled`` ``False``.
    """
    if secure_mode:
        return RatePolicyDecision(
            auto_enabled=False,
            triggers=posture.triggers,
            limits=operator_limits,
            filled=(),
            report_lines=("flood auto-enable deferred to --secure (preset normalises limits)",),
        )

    triggers = posture.triggers
    if not triggers:
        return RatePolicyDecision(
            auto_enabled=False,
            triggers=(),
            limits=operator_limits,
            filled=(),
            report_lines=(
                "flood auto-enable not triggered: local-first posture "
                "(loopback bind, no token, no bridge, single seat)",
            ),
        )

    filled: list[str] = []

    if _rate_disabled(operator_limits.agent_rate):
        agent_rate, agent_burst = SECURE_AGENT_RATE, SECURE_AGENT_BURST
        filled.append("per-agent rate")
    else:
        agent_rate, agent_burst = operator_limits.agent_rate, operator_limits.agent_burst

    if _rate_disabled(operator_limits.host_rate):
        host_rate, host_burst = SECURE_HOST_RATE, SECURE_HOST_BURST
        filled.append("per-host rate")
    else:
        host_rate, host_burst = operator_limits.host_rate, operator_limits.host_burst

    if operator_limits.max_connections_per_host <= 0:
        max_connections_per_host = SECURE_MAX_CONNECTIONS_PER_HOST
        filled.append("per-host connection cap")
    else:
        max_connections_per_host = operator_limits.max_connections_per_host

    limits = RateLimits(
        agent_rate=agent_rate,
        agent_burst=agent_burst,
        host_rate=host_rate,
        host_burst=host_burst,
        max_connections_per_host=max_connections_per_host,
    )
    trigger_summary = ", ".join(triggers)
    report_lines: tuple[str, ...]
    if filled:
        report_lines = (
            f"flood auto-enable triggered by: {trigger_summary}",
            f"auto-filled bounded limits: {', '.join(filled)}",
            (
                f"effective: per-agent {limits.agent_rate:g}/s burst {limits.agent_burst:g}, "
                f"per-host {limits.host_rate:g}/s burst {limits.host_burst:g}, "
                f"connections/host {limits.max_connections_per_host}"
            ),
        )
    else:
        report_lines = (
            f"exposed posture ({trigger_summary}); all flood limits already set by the operator",
        )
    return RatePolicyDecision(
        auto_enabled=bool(filled),
        triggers=triggers,
        limits=limits,
        filled=tuple(filled),
        report_lines=report_lines,
    )
