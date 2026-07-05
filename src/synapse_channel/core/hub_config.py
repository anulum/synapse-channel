# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — grouped, typed construction record for SynapseHub
"""Grouped configuration record for :class:`~synapse_channel.core.hub.SynapseHub`.

``SynapseHub.__init__`` accepts every knob as one flat keyword surface — the
right shape for the CLI, which maps flags one-to-one, but a heavy burden for a
library consumer who embeds a hub and wants to see which of the forty-odd
parameters belong together. :class:`HubConfig` groups them into their opt-in
families — ceilings (:class:`HubLimits`), name-takeover damping
(:class:`TakeoverDamping`), authentication and access control
(:class:`HubAuthConfig`), the HTTP metrics endpoint
(:class:`HubMetricsConfig`), multi-hub claim routing (:class:`MultiHubConfig`),
and cross-domain federation (:class:`FederationConfig`) — while
:meth:`HubConfig.to_kwargs` flattens the record back into exactly the keyword
arguments ``SynapseHub.__init__`` accepts. Behaviour is identical by
construction: every field name matches its keyword parameter, every default
mirrors the parameter default, and contract tests pin both against the live
signature so the two surfaces cannot drift apart.

Construct a hub from a record with
:meth:`~synapse_channel.core.hub.SynapseHub.from_config`::

    config = HubConfig(
        auth=HubAuthConfig(authenticator=TokenAuthenticator(["secret"])),
        limits=HubLimits(max_clients=32),
    )
    hub = SynapseHub.from_config(config)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.hub import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_COMPACT_HINT_THRESHOLD,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_FINDINGS_PER_AGENT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
    DEFAULT_TAKEOVER_COOLDOWN,
    DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD,
    DEFAULT_TAKEOVER_OSCILLATION_WINDOW,
    DEFAULT_TAKEOVER_QUARANTINE,
)
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
)
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureTrustBundle,
    MessageAuthKey,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardPeer,
    forward_claim,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingPolicy,
    PeerCertificateSource,
    live_peer_certificate_der,
)
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayForwarder,
    relay_operator_action,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT, MAX_OFFERS_PER_AGENT

__all__ = [
    "FederationConfig",
    "HubAuthConfig",
    "HubConfig",
    "HubLimits",
    "HubMetricsConfig",
    "MultiHubConfig",
    "TakeoverDamping",
]


@dataclass(frozen=True, kw_only=True)
class HubLimits:
    """Every ceiling the hub enforces: retention, quotas, and transport bounds.

    Field names and defaults match the ``SynapseHub.__init__`` keyword
    parameters of the same names; see that signature's documentation for the
    meaning and failure mode of each bound.
    """

    max_history: int = DEFAULT_MAX_HISTORY
    max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES
    max_clients: int = DEFAULT_MAX_CLIENTS
    max_unauth_clients: int | None = None
    max_connections_per_host: int | None = None
    max_progress: int = DEFAULT_MAX_PROGRESS
    max_progress_per_author: int = DEFAULT_MAX_PROGRESS_PER_AUTHOR
    max_progress_per_task: int = DEFAULT_MAX_PROGRESS_PER_TASK
    board_task_cap: int | None = None
    max_findings_per_agent: int = DEFAULT_MAX_FINDINGS_PER_AGENT
    max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT
    max_offers_per_agent: int = MAX_OFFERS_PER_AGENT
    max_paths_per_claim: int = MAX_DECLARED_PATHS
    compact_hint_threshold: int = DEFAULT_COMPACT_HINT_THRESHOLD


@dataclass(frozen=True, kw_only=True)
class TakeoverDamping:
    """Damping applied when one agent name is taken over repeatedly.

    Cooldown blunts a single eviction storm; the oscillation window and
    threshold detect two waiters at war over one name; quarantine pins a
    thrashing name to its current owner.
    """

    takeover_cooldown: float = DEFAULT_TAKEOVER_COOLDOWN
    takeover_oscillation_window: float = DEFAULT_TAKEOVER_OSCILLATION_WINDOW
    takeover_oscillation_threshold: int = DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD
    takeover_quarantine: float = DEFAULT_TAKEOVER_QUARANTINE


@dataclass(frozen=True, kw_only=True)
class HubAuthConfig:
    """Connection authentication, per-message authentication, and ACL enforcement.

    Everything here defaults to the open loopback posture: no token, no
    signed frames, no ACL. Each mechanism is opt-in and composes with the
    others exactly as the flat keyword surface documents.
    """

    authenticator: TokenAuthenticator | None = None
    auth_timeout: float = DEFAULT_AUTH_TIMEOUT
    insecure_off_loopback: bool = False
    per_message_auth_keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None = None
    require_per_message_auth: bool = False
    per_message_auth_window_seconds: float = DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS
    per_message_auth_replay_capacity: int = 4096
    signed_event_trust_bundle: EventSignatureTrustBundle | None = None
    acl_policy: AclPolicy | None = None
    require_acl: bool = False


@dataclass(frozen=True, kw_only=True)
class HubMetricsConfig:
    """The optional HTTP ``/metrics`` and ``/health`` endpoint and its token."""

    enable_metrics: bool = False
    metrics_token: str | None = None
    metrics_query_token_ok: bool = False


@dataclass(frozen=True, kw_only=True)
class MultiHubConfig:
    """Multi-hub routing: serving policy, namespace ownership, claim and relay forwarding."""

    multihub_serving_policy: MultiHubServingPolicy | None = None
    namespace_ownership: NamespaceOwnership | None = None
    claim_peers: Mapping[str, ClaimForwardPeer] | None = None
    claim_forwarder: ClaimForwarder = forward_claim
    relay_peers: Mapping[str, OperatorRelayPeer] | None = None
    relay_forwarder: RelayForwarder = relay_operator_action
    require_relay_reason: bool = False
    observed_asserting_hubs: Callable[[str], Iterable[str]] | None = None


@dataclass(frozen=True, kw_only=True)
class FederationConfig:
    """Cross-domain federation: the peering bundle, certificate reader, and served offer."""

    federation_bundle: FederationBundle | None = None
    federation_cert_source: PeerCertificateSource = live_peer_certificate_der
    federation_offer_path: str | Path | None = None


#: HubConfig attributes holding nested family records, mapped to their record type.
_FAMILY_TYPES: dict[str, type[Any]] = {
    "limits": HubLimits,
    "takeover": TakeoverDamping,
    "auth": HubAuthConfig,
    "metrics": HubMetricsConfig,
    "multihub": MultiHubConfig,
    "federation": FederationConfig,
}

#: HubConfig attributes holding nested family records rather than direct kwargs.
_FAMILY_FIELDS = tuple(_FAMILY_TYPES)


@dataclass(frozen=True, kw_only=True)
class HubConfig:
    """Complete, grouped construction record for one :class:`SynapseHub`.

    The direct fields cover the hub's identity and collaborators; the nested
    family records cover the opt-in surfaces. ``HubConfig()`` reproduces a
    bare ``SynapseHub()`` exactly.
    """

    default_ttl_seconds: float = 3600.0
    hub_id: str | None = None
    journal: EventStore | None = None
    clock: Callable[[], float] | None = None
    rate_limiter: RateLimiter | None = None
    host_rate_limiter: RateLimiter | None = None
    relay_log: str | Path | None = None
    relay_max_lines: int = DEFAULT_RELAY_MAX_LINES
    shutdown_close_timeout: float = DEFAULT_SHUTDOWN_CLOSE_TIMEOUT
    limits: HubLimits = field(default_factory=HubLimits)
    takeover: TakeoverDamping = field(default_factory=TakeoverDamping)
    auth: HubAuthConfig = field(default_factory=HubAuthConfig)
    metrics: HubMetricsConfig = field(default_factory=HubMetricsConfig)
    multihub: MultiHubConfig = field(default_factory=MultiHubConfig)
    federation: FederationConfig = field(default_factory=FederationConfig)

    def to_kwargs(self) -> dict[str, Any]:
        """Flatten the record into the keyword arguments ``SynapseHub`` accepts.

        Returns
        -------
        dict[str, Any]
            One entry per ``SynapseHub.__init__`` keyword parameter: the
            nested family fields spread under their own names, then the
            direct fields. Contract tests pin the key set and the defaults
            against the live signature.
        """
        kwargs: dict[str, Any] = {}
        for family_name in _FAMILY_FIELDS:
            family = getattr(self, family_name)
            for spec in fields(family):
                kwargs[spec.name] = getattr(family, spec.name)
        for spec in fields(self):
            if spec.name not in _FAMILY_FIELDS:
                kwargs[spec.name] = getattr(self, spec.name)
        return kwargs

    @classmethod
    def from_kwargs(cls, kwargs: Mapping[str, Any]) -> HubConfig:
        """Re-group flat ``SynapseHub`` keyword arguments into a record.

        The inverse of :meth:`to_kwargs`: each family field regroups under its
        family, every other key is a direct field, and an omitted key takes its
        default — so a caller can hand over the **partial** keyword set it
        actually assembled (for example the CLI's subset of the ~40 parameters)
        and still get a complete record to fingerprint or reconstruct from. On
        the full key set it round-trips with :meth:`to_kwargs`.

        Raises
        ------
        TypeError
            If ``kwargs`` carries a key that is neither a family field nor a
            direct ``SynapseHub`` parameter.
        """
        remaining = dict(kwargs)
        grouped: dict[str, Any] = {}
        for family_name, family_cls in _FAMILY_TYPES.items():
            family_field_names = {spec.name for spec in fields(family_cls)}
            grouped[family_name] = family_cls(
                **{
                    name: remaining.pop(name)
                    for name in list(remaining)
                    if name in family_field_names
                }
            )
        return cls(**grouped, **remaining)


def config_fingerprint(config: HubConfig) -> str:
    """Return a stable fingerprint of the hub's effective configuration posture.

    A short, deterministic digest a cockpit can pin: while it stays the same the
    hub is running the configuration it started with, and a change is a config
    drift an operator should notice (a limit raised, a subsystem armed or
    disarmed). It is derived from the six configuration families —
    :class:`HubLimits`, :class:`TakeoverDamping`, :class:`HubAuthConfig`,
    :class:`HubMetricsConfig`, :class:`MultiHubConfig`, :class:`FederationConfig` —
    so a new configuration parameter is covered the moment it joins a family.

    Deterministic by construction: scalar fields (the limits, timeouts, and the
    ``require_*`` / ``enable_*`` posture toggles) enter by value, while object
    fields (an authenticator, an ACL policy, a federation bundle) enter only as a
    **presence marker** naming their type — never their identity or contents. So
    the same posture always yields the same fingerprint across restarts, and no
    secret material is hashed.

    Honest scope: it fingerprints *posture*, not secrets. Arming or disarming a
    subsystem, or changing a numeric bound, changes the fingerprint; rotating a
    key or editing an ACL rule while the posture is unchanged does not — that is a
    credential-rotation concern, not a configuration-drift one.
    """
    posture: dict[str, object] = {}
    for family_name in _FAMILY_FIELDS:
        family = getattr(config, family_name)
        for spec in fields(family):
            value = getattr(family, spec.name)
            key = f"{family_name}.{spec.name}"
            if value is None or isinstance(value, (bool, int, float, str)):
                posture[key] = value
            else:
                posture[key] = f"<set:{type(value).__name__}>"
    canonical = json.dumps(posture, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
