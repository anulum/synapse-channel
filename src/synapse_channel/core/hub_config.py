# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — grouped, typed construction record for SynapseHub
"""Grouped configuration record for :class:`~synapse_channel.core.hub.SynapseHub`.

``SynapseHub.__init__`` accepts a :class:`HubConfig` record as its canonical
input. Existing keyword callers remain supported through the typed
``HubLegacyOptions`` boundary. :class:`HubConfig` composes seven family records
owned by their area modules — ceilings (:class:`HubLimits`) and name-takeover
damping (:class:`TakeoverDamping`) in :mod:`~synapse_channel.core.hub_config_limits`,
authentication and access control (:class:`HubAuthConfig`) in
:mod:`~synapse_channel.core.hub_config_auth`, the HTTP metrics endpoint
(:class:`HubMetricsConfig`) and stale-recipient delivery gate (:class:`HubLiveness`)
in :mod:`~synapse_channel.core.hub_config_metrics`, and multi-hub claim routing
(:class:`MultiHubConfig`) and cross-domain federation (:class:`FederationConfig`)
in :mod:`~synapse_channel.core.hub_config_routing`. Family records own defaults.
The hub retains normalized settings in its configuration record and keeps live
state separately. :meth:`HubConfig.to_kwargs` supplies the original keyword
surface for embedding subclasses and other legacy callers. Contract tests pin
all 86 original names, types and defaults independently.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.hub_config_auth import HubAuthConfig
from synapse_channel.core.hub_config_limits import HubLimits, TakeoverDamping
from synapse_channel.core.hub_config_metrics import HubLiveness, HubMetricsConfig
from synapse_channel.core.hub_config_routing import FederationConfig, MultiHubConfig
from synapse_channel.core.hub_defaults import (
    DEFAULT_FEED_CHAT_MARKERS,
    DEFAULT_RELAY_MAX_LINES,
    DEFAULT_SHUTDOWN_CLOSE_TIMEOUT,
)
from synapse_channel.core.merkle_checkpoint import DEFAULT_CHECKPOINT_INTERVAL
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission_journal import ProtectedAdmissionReplayPolicy
from synapse_channel.core.ratelimit import RateLimiter

__all__ = [
    "FederationConfig",
    "HubAuthConfig",
    "HubConfig",
    "HubLimits",
    "HubLiveness",
    "HubMetricsConfig",
    "MultiHubConfig",
    "TakeoverDamping",
]

#: HubConfig attributes holding nested family records, mapped to their record type.
_FAMILY_TYPES: dict[str, type[Any]] = {
    "limits": HubLimits,
    "takeover": TakeoverDamping,
    "auth": HubAuthConfig,
    "metrics": HubMetricsConfig,
    "liveness": HubLiveness,
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

    ``protected_write_policies`` retains the constructor's optional enrollment
    replay policies. Its default is ``None``; supplying replay policy does not
    register protected-write dispatch or activate a privileged writer.
    """

    default_ttl_seconds: float = 3600.0
    hub_id: str | None = None
    journal: EventStore | None = None
    attachment_store: AttachmentStore | None = None
    attachment_serving_policy: AttachmentServingPolicy | None = None
    anti_rollback_checkpoint: bool = True
    checkpoint_store_path: str | Path | None = None
    checkpoint_interval: float = DEFAULT_CHECKPOINT_INTERVAL
    protected_write_policies: Mapping[str, ProtectedAdmissionReplayPolicy] | None = None
    clock: Callable[[], float] | None = None
    rate_limiter: RateLimiter | None = None
    host_rate_limiter: RateLimiter | None = None
    durable_ingress_quota: DurableIngressQuota | None = None
    relay_log: str | Path | None = None
    relay_max_lines: int = DEFAULT_RELAY_MAX_LINES
    feed_chat_markers: tuple[str, ...] = DEFAULT_FEED_CHAT_MARKERS
    shutdown_close_timeout: float = DEFAULT_SHUTDOWN_CLOSE_TIMEOUT
    limits: HubLimits = field(default_factory=HubLimits)
    takeover: TakeoverDamping = field(default_factory=TakeoverDamping)
    auth: HubAuthConfig = field(default_factory=HubAuthConfig)
    metrics: HubMetricsConfig = field(default_factory=HubMetricsConfig)
    liveness: HubLiveness = field(default_factory=HubLiveness)
    multihub: MultiHubConfig = field(default_factory=MultiHubConfig)
    federation: FederationConfig = field(default_factory=FederationConfig)

    def to_kwargs(self) -> dict[str, Any]:
        """Flatten the record into the keyword arguments ``SynapseHub`` accepts.

        Returns
        -------
        dict[str, Any]
            One entry per original ``SynapseHub`` keyword option: the
            nested family fields spread under their own names, then the
            direct fields. Contract tests pin the key set and the defaults
            against the typed compatibility boundary and original defaults.
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
        actually assembled (for example an embedding caller's subset)
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
    posture["attachment_store"] = config.attachment_store is not None
    posture["attachment_serving_policy"] = config.attachment_serving_policy is not None
    for family_name in _FAMILY_FIELDS:
        family = getattr(config, family_name)
        for spec in fields(family):
            value = getattr(family, spec.name)
            key = f"{family_name}.{spec.name}"
            if key == "metrics.metrics_token":
                posture[key] = "<set:metrics-token>" if value else None
            elif value is None or isinstance(value, (bool, int, float, str)):
                posture[key] = value
            else:
                posture[key] = f"<set:{type(value).__name__}>"
    canonical = json.dumps(posture, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
