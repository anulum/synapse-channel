# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — contract tests pinning HubConfig to the SynapseHub signature

from __future__ import annotations

import dataclasses
import inspect
from collections import Counter

import pytest

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import (
    _FAMILY_FIELDS,
    FederationConfig,
    HubAuthConfig,
    HubConfig,
    HubLimits,
    HubMetricsConfig,
    MultiHubConfig,
    TakeoverDamping,
)


def _init_parameters() -> dict[str, inspect.Parameter]:
    parameters = dict(inspect.signature(SynapseHub.__init__).parameters)
    parameters.pop("self")
    return parameters


def test_to_kwargs_covers_exactly_the_init_signature() -> None:
    # A parameter added to SynapseHub.__init__ without a HubConfig field (or
    # the reverse) must fail here, so the two surfaces cannot drift apart.
    assert set(HubConfig().to_kwargs()) == set(_init_parameters())


def test_no_field_name_is_claimed_twice() -> None:
    # to_kwargs merges the families into one dict; a name owned by two
    # families would silently shadow one value.
    config = HubConfig()
    names = Counter(
        spec.name
        for family_name in _FAMILY_FIELDS
        for spec in dataclasses.fields(getattr(config, family_name))
    )
    names.update(
        spec.name for spec in dataclasses.fields(config) if spec.name not in _FAMILY_FIELDS
    )
    duplicated = [name for name, count in names.items() if count > 1]
    assert not duplicated, f"fields claimed by more than one family: {duplicated}"


def test_every_default_mirrors_the_init_default() -> None:
    kwargs = HubConfig().to_kwargs()
    for name, parameter in _init_parameters().items():
        assert kwargs[name] == parameter.default, name


def test_default_config_builds_the_same_hub_as_bare_construction() -> None:
    from_config = SynapseHub.from_config()
    bare = SynapseHub()
    for attribute in (
        "max_history",
        "max_msg_bytes",
        "max_clients",
        "max_unauth_clients",
        "max_connections_per_host",
        "max_findings_per_agent",
        "compact_hint_threshold",
        "takeover_cooldown",
        "takeover_oscillation_window",
        "takeover_oscillation_threshold",
        "takeover_quarantine",
        "shutdown_close_timeout",
        "auth_timeout",
        "enable_metrics",
        "metrics_token",
        "metrics_query_token_ok",
        "insecure_off_loopback",
        "require_per_message_auth",
        "require_acl",
        "authenticator",
        "acl_policy",
        "rate_limiter",
        "host_rate_limiter",
        "relay_log",
        "relay_max_lines",
        "journal",
        "multihub_serving_policy",
        "namespace_ownership",
        "claim_peers",
        "claim_forwarder",
        "observed_asserting_hubs",
        "federation_bundle",
        "federation_cert_source",
        "signed_event_trust_bundle",
        "per_message_auth_keys",
    ):
        assert getattr(from_config, attribute) == getattr(bare, attribute), attribute


def test_family_overrides_reach_the_constructed_hub() -> None:
    def cert_source(_websocket: object) -> bytes | None:
        return None

    def asserting(_namespace: str) -> list[str]:
        return []

    config = HubConfig(
        hub_id="cfg-hub",
        default_ttl_seconds=120.0,
        limits=HubLimits(max_history=5, max_clients=7, max_findings_per_agent=3),
        takeover=TakeoverDamping(takeover_cooldown=9.0, takeover_quarantine=99.0),
        auth=HubAuthConfig(require_acl=True, auth_timeout=3.5),
        metrics=HubMetricsConfig(enable_metrics=True, metrics_token="mtok"),
        multihub=MultiHubConfig(observed_asserting_hubs=asserting),
        federation=FederationConfig(federation_cert_source=cert_source),
    )
    hub = SynapseHub.from_config(config)
    assert hub.hub_id == "cfg-hub"
    assert hub.max_history == 5
    assert hub.max_clients == 7
    assert hub.max_findings_per_agent == 3
    assert hub.takeover_cooldown == 9.0
    assert hub.takeover_quarantine == 99.0
    assert hub.require_acl is True
    assert hub.auth_timeout == 3.5
    assert hub.enable_metrics is True
    assert hub.metrics_token == "mtok"
    assert hub.observed_asserting_hubs is asserting
    assert hub.federation_cert_source is cert_source
    assert hub.state.default_ttl_seconds == 120.0


def test_records_are_frozen() -> None:
    # setattr through a variable name, so the frozen refusal is exercised at
    # runtime rather than rejected by the type checker.
    config = HubConfig()
    for record, attribute in ((config, "hub_id"), (config.limits, "max_history")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, attribute, "overwritten")


def test_from_config_accepts_an_explicit_record() -> None:
    hub = SynapseHub.from_config(HubConfig(hub_id="explicit"))
    assert isinstance(hub, SynapseHub)
    assert hub.hub_id == "explicit"
