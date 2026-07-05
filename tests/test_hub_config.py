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

from synapse_channel.core.auth import TokenAuthenticator
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
    config_fingerprint,
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


class TestConfigFingerprint:
    def test_is_deterministic_and_a_short_hex_digest(self) -> None:
        first = config_fingerprint(HubConfig())
        second = config_fingerprint(HubConfig())
        assert first == second
        assert len(first) == 16
        assert all(character in "0123456789abcdef" for character in first)

    def test_tracks_a_scalar_posture_change(self) -> None:
        base = config_fingerprint(HubConfig())
        raised = config_fingerprint(HubConfig(limits=HubLimits(max_clients=99)))
        assert raised != base

    def test_tracks_arming_an_optional_subsystem(self) -> None:
        open_posture = config_fingerprint(HubConfig())
        acl_armed = config_fingerprint(HubConfig(auth=HubAuthConfig(require_acl=True)))
        assert acl_armed != open_posture

    def test_is_presence_only_for_objects_never_their_identity(self) -> None:
        # Two hubs that both have *an* authenticator share a posture, so they
        # share a fingerprint — the digest marks presence, never the secret or
        # the object identity. Arming auth at all does change it.
        armed_a = config_fingerprint(
            HubConfig(auth=HubAuthConfig(authenticator=TokenAuthenticator(["alpha"])))
        )
        armed_b = config_fingerprint(
            HubConfig(auth=HubAuthConfig(authenticator=TokenAuthenticator(["beta"])))
        )
        unarmed = config_fingerprint(HubConfig())
        assert armed_a == armed_b
        assert armed_a != unarmed

    def test_from_config_stamps_the_epoch_and_an_adhoc_hub_is_empty(self) -> None:
        config = HubConfig(limits=HubLimits(max_clients=7))
        configured = SynapseHub.from_config(config)
        assert configured.config_epoch == config_fingerprint(config)
        # A bare construction was not built from a record, so it carries no epoch.
        assert SynapseHub().config_epoch == ""


class TestFromKwargs:
    def test_round_trips_with_to_kwargs_on_the_full_key_set(self) -> None:
        original = HubConfig(
            hub_id="rt",
            limits=HubLimits(max_clients=17, max_history=9),
            auth=HubAuthConfig(require_acl=True, auth_timeout=4.0),
            metrics=HubMetricsConfig(enable_metrics=True, metrics_token="t"),
        )
        assert HubConfig.from_kwargs(original.to_kwargs()) == original

    def test_regroups_a_partial_flat_kwarg_set_filling_defaults(self) -> None:
        # The CLI hands over only the subset it assembled; the rest must default.
        config = HubConfig.from_kwargs(
            {"max_clients": 42, "require_acl": True, "hub_id": "cli", "enable_metrics": True}
        )
        assert config.limits.max_clients == 42
        assert config.auth.require_acl is True
        assert config.metrics.enable_metrics is True
        assert config.hub_id == "cli"
        # An unspecified field keeps its family default.
        assert config.limits.max_history == HubLimits().max_history

    def test_an_unknown_key_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            HubConfig.from_kwargs({"not_a_hub_parameter": 1})

    def test_fingerprint_of_the_regrouped_record_matches_the_original(self) -> None:
        original = HubConfig(limits=HubLimits(max_clients=8), auth=HubAuthConfig(require_acl=True))
        regrouped = HubConfig.from_kwargs(original.to_kwargs())
        assert config_fingerprint(regrouped) == config_fingerprint(original)
