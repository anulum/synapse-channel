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
import math
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

import synapse_channel
from hub_legacy_defaults import LEGACY_DEFAULTS
from synapse_channel.core import hub_config as hub_config_module
from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.approval_load import ApprovalLoad
from synapse_channel.core.approval_load_policy import ApprovalLoadPolicy
from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.hub import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_MSG_BYTES,
    DEFAULT_RELAY_MAX_LINES,
    SynapseHub,
)
from synapse_channel.core.hub_config import (
    _FAMILY_FIELDS,
    HubConfig,
    config_fingerprint,
)
from synapse_channel.core.hub_config_auth import HubAuthConfig
from synapse_channel.core.hub_config_limits import HubLimits, TakeoverDamping
from synapse_channel.core.hub_config_metrics import HubLiveness, HubMetricsConfig
from synapse_channel.core.hub_config_routing import FederationConfig, MultiHubConfig
from synapse_channel.core.hub_constructor_options import HubLegacyOptions
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.message_forward_transport import forward_message
from synapse_channel.core.multihub_claim_transport import forward_claim
from synapse_channel.core.multihub_serving import live_peer_certificate_der
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import relay_operator_action


def _init_parameters() -> dict[str, inspect.Parameter]:
    parameters = dict(inspect.signature(SynapseHub.__init__).parameters)
    parameters.pop("self")
    return parameters


def test_liveness_states_the_legacy_socket_presence_behaviour() -> None:
    """The shipped liveness policy states the legacy socket-presence behaviour."""
    text = " ".join((HubLiveness.__doc__ or "").split())
    assert "legacy socket-presence behaviour" in text
    assert "behavior" not in text


def test_to_kwargs_covers_exactly_the_init_signature() -> None:
    # All original keyword names remain accepted through the typed boundary;
    # the actual constructor now receives one canonical record.
    assert set(HubConfig().to_kwargs()) == set(LEGACY_DEFAULTS)
    assert set(HubLegacyOptions.__annotations__) == set(LEGACY_DEFAULTS)
    parameters = _init_parameters()
    assert set(parameters) == {"config", "legacy"}
    assert parameters["legacy"].kind is inspect.Parameter.VAR_KEYWORD


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
    assert HubConfig().to_kwargs() == LEGACY_DEFAULTS
    assert SynapseHub().configuration.to_kwargs() == LEGACY_DEFAULTS


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
        "metrics_token_set",
        "metrics_query_token_ok",
        "insecure_off_loopback",
        "insecure_plaintext_at_rest",
        "require_per_message_auth",
        "per_message_auth_replay_store",
        "per_message_auth_sequence_floor_mode",
        "require_acl",
        "authenticator",
        "acl_policy",
        "rate_limiter",
        "host_rate_limiter",
        "durable_ingress_quota",
        "relay_log",
        "relay_max_lines",
        "journal",
        "multihub_serving_policy",
        "namespace_ownership",
        "claim_peers",
        "claim_forwarder",
        "relay_peers",
        "relay_forwarder",
        "require_relay_reason",
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
    assert hub.observed_asserting_hubs("BENCH") == []
    assert hub.federation_cert_source is cert_source
    assert hub.federation_cert_source(object()) is None
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


def test_hub_construction_coerces_non_finite_numeric_bounds() -> None:
    hub = SynapseHub(
        max_history=cast(int, float("inf")),
        max_msg_bytes=cast(int, float("inf")),
        relay_max_lines=cast(int, float("nan")),
        auth_timeout=float("nan"),
        recipient_liveness_window=float("inf"),
        waiter_liveness_window=cast(float, 10**400),
    )

    assert hub.max_history == DEFAULT_MAX_HISTORY
    assert hub.max_msg_bytes == DEFAULT_MAX_MSG_BYTES
    assert hub.relay_max_lines == DEFAULT_RELAY_MAX_LINES
    assert hub.auth_timeout == DEFAULT_AUTH_TIMEOUT
    assert math.isfinite(hub.recipient_liveness_window)
    assert math.isfinite(hub.waiter_liveness_window)


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

    def test_recipient_policy_changes_posture_without_hashing_its_path(
        self, tmp_path: Path
    ) -> None:
        first = config_fingerprint(
            HubConfig(attachment_serving_policy=AttachmentServingPolicy(tmp_path / "first"))
        )
        rotated = config_fingerprint(
            HubConfig(attachment_serving_policy=AttachmentServingPolicy(tmp_path / "rotated"))
        )
        assert first != config_fingerprint(HubConfig())
        assert first == rotated

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


def test_the_hub_reads_its_identity_pin_path_from_its_pin_store(tmp_path: Path) -> None:
    pins = tmp_path / "pins.json"
    pinned = SynapseHub.from_config(HubConfig(auth=HubAuthConfig(identity_pin_path=str(pins))))
    unpinned = SynapseHub.from_config(HubConfig())
    assert pinned.identity_pin_path == pins == pinned.identity_pins.path
    assert unpinned.identity_pin_path is None


def _armed_hub_config() -> HubConfig:
    """Build a HubConfig with every family set to non-defaults and live objects present."""
    load = ApprovalLoad(ApprovalLoadPolicy(roles={}, approvers={}))
    return HubConfig(
        hub_id="armed-hub",
        default_ttl_seconds=120.0,
        limits=HubLimits(max_history=50, max_clients=32, board_task_cap=200),
        takeover=TakeoverDamping(takeover_cooldown=9.0, takeover_quarantine=99.0),
        auth=HubAuthConfig(
            authenticator=TokenAuthenticator(["tok"]),
            require_acl=True,
            acl_policy=AclPolicy([]),
            approval_load=load,
            ladder_enabled=True,
        ),
        metrics=HubMetricsConfig(enable_metrics=True, metrics_token="mtok"),
        liveness=HubLiveness(warn_stale_recipients=False, recipient_liveness_window=45.0),
        multihub=MultiHubConfig(
            namespace_ownership=NamespaceOwnership({"P": "armed-hub"}, "armed-hub"),
            require_relay_reason=True,
        ),
        federation=FederationConfig(
            federation_bundle=FederationBundle(peers=()),
            federation_offer_path=Path("offer.json"),
        ),
    )


def _field_default(spec: dataclasses.Field[object]) -> object:
    if spec.default is not dataclasses.MISSING:
        return spec.default
    factory = spec.default_factory
    assert factory is not dataclasses.MISSING
    factory_name = getattr(factory, "__name__", repr(factory))
    return f"<factory:{factory_name}>"


def test_golden_fingerprints_default_and_armed() -> None:
    """Golden config_fingerprint digests stay byte-identical for default and fully armed configs."""
    assert config_fingerprint(HubConfig()) == "31b850cb018860e8"
    assert config_fingerprint(_armed_hub_config()) == "a563a541952e27ca"


def test_golden_family_field_tables_order_and_defaults() -> None:
    """Pin exact field order and defaults for every configuration family record."""
    expected: tuple[
        tuple[str, tuple[dataclasses.Field[object], ...], list[tuple[str, object]]], ...
    ] = (
        (
            "HubLimits",
            dataclasses.fields(HubLimits),
            [
                ("max_history", 10000),
                ("max_msg_bytes", 1048576),
                ("max_clients", 256),
                ("max_unauth_clients", None),
                ("max_connections_per_host", 32),
                ("max_progress", 5000),
                ("max_progress_per_author", 1000),
                ("max_progress_per_task", 1000),
                ("board_task_cap", None),
                ("max_findings_per_agent", 512),
                ("max_claims_per_agent", 128),
                ("max_offers_per_agent", 64),
                ("max_paths_per_claim", 512),
                ("compact_hint_threshold", 100000),
                ("dead_letter_escalation_threshold", 0),
            ],
        ),
        (
            "TakeoverDamping",
            dataclasses.fields(TakeoverDamping),
            [
                ("takeover_cooldown", 2.0),
                ("takeover_oscillation_window", 30.0),
                ("takeover_oscillation_threshold", 5),
                ("takeover_quarantine", 60.0),
                ("lease_offline_ttl", 3600.0),
            ],
        ),
        (
            "HubAuthConfig",
            dataclasses.fields(HubAuthConfig),
            [
                ("authenticator", None),
                ("auth_timeout", 10.0),
                ("insecure_off_loopback", False),
                ("insecure_plaintext_at_rest", False),
                ("per_message_auth_keys", None),
                ("require_per_message_auth", False),
                ("per_message_auth_window_seconds", 10.0),
                ("per_message_auth_replay_capacity", 4096),
                ("per_message_auth_replay_store", None),
                ("per_message_auth_sequence_floor_mode", SequenceFloorMode.OFF),
                ("signed_event_trust_bundle", None),
                ("capability_card_trust_bundle", None),
                ("acl_policy", None),
                ("require_acl", False),
                ("role_grants", None),
                ("approval_load", None),
                ("mailbox_custodians", None),
                ("require_role_claim", False),
                ("require_fencing_epoch", False),
                ("require_signed_frames", False),
                ("require_identity_proof", False),
                ("identity_trust_bundle", None),
                ("require_identity_binding", False),
                ("identity_pin_path", None),
                ("identity_enrollment_path", None),
                ("identity_enrollment_namespaces", ()),
                ("identity_enrollment_rate", 10),
                ("identity_enrollment_window_seconds", 3600.0),
                ("private_directed_messages", False),
                ("verdict_authority", "<factory:dict>"),
                ("verdict_gate_enabled", True),
                ("allow_unrecorded_verdict_inputs", False),
                ("ladder_enabled", False),
                ("stop_bound_seconds", 900.0),
            ],
        ),
        (
            "HubMetricsConfig",
            dataclasses.fields(HubMetricsConfig),
            [
                ("enable_metrics", False),
                ("metrics_token", None),
                ("metrics_token_set", None),
                ("metrics_query_token_ok", False),
                ("allowed_origins", ()),
                ("advertised_host", None),
            ],
        ),
        (
            "HubLiveness",
            dataclasses.fields(HubLiveness),
            [
                ("warn_stale_recipients", True),
                ("recipient_liveness_window", 90.0),
                ("waiter_liveness_window", 20.0),
            ],
        ),
        (
            "MultiHubConfig",
            dataclasses.fields(MultiHubConfig),
            [
                ("multihub_serving_policy", None),
                ("spend_ledger", None),
                ("namespace_ownership", None),
                ("claim_peers", None),
                ("claim_forwarder", forward_claim),
                ("relay_peers", None),
                ("relay_forwarder", relay_operator_action),
                ("message_peers", None),
                ("message_forwarder", forward_message),
                ("message_forward_ttl", 86400.0),
                ("require_relay_reason", False),
                ("require_two_person_relay", False),
                ("observed_asserting_hubs", None),
                ("dead_letter_forwarder", forward_dead_letter),
            ],
        ),
        (
            "FederationConfig",
            dataclasses.fields(FederationConfig),
            [
                ("federation_bundle", None),
                ("federation_cert_source", live_peer_certificate_der),
                ("federation_offer_path", None),
            ],
        ),
    )
    for name, field_specs, expected_table in expected:
        actual = [(spec.name, _field_default(spec)) for spec in field_specs]
        assert actual == expected_table, name


def test_armed_kwargs_round_trip_and_legacy_flat_keywords() -> None:
    """Every family round-trips through to_kwargs/from_kwargs and legacy flat keywords."""
    armed = _armed_hub_config()
    flat = armed.to_kwargs()
    assert HubConfig.from_kwargs(flat) == armed

    subset = HubConfig.from_kwargs(
        {
            "hub_id": "flat-hub",
            "max_history": 50,
            "takeover_cooldown": 9.0,
            "require_acl": True,
            "enable_metrics": True,
            "warn_stale_recipients": False,
            "require_relay_reason": True,
            "federation_offer_path": Path("offer.json"),
        }
    )
    assert subset.hub_id == "flat-hub"
    assert subset.limits.max_history == 50
    assert subset.takeover.takeover_cooldown == 9.0
    assert subset.auth.require_acl is True
    assert subset.metrics.enable_metrics is True
    assert subset.liveness.warn_stale_recipients is False
    assert subset.multihub.require_relay_reason is True
    assert subset.federation.federation_offer_path == Path("offer.json")


def test_public_top_level_names_match_family_classes() -> None:
    """Top-level exports resolve to the owning classes; HubLiveness stays private."""
    assert synapse_channel.HubConfig is HubConfig
    assert synapse_channel.HubAuthConfig is HubAuthConfig
    assert synapse_channel.HubLimits is HubLimits
    assert synapse_channel.TakeoverDamping is TakeoverDamping
    assert synapse_channel.HubMetricsConfig is HubMetricsConfig
    assert synapse_channel.MultiHubConfig is MultiHubConfig
    assert synapse_channel.FederationConfig is FederationConfig
    assert not hasattr(synapse_channel, "HubLiveness")


def test_family_records_live_in_area_modules_and_reexport_identically() -> None:
    """Records live in their area modules and core.hub_config re-exports the exact objects."""
    assert hub_config_module.__all__ == [
        "FederationConfig",
        "HubAuthConfig",
        "HubConfig",
        "HubLimits",
        "HubLiveness",
        "HubMetricsConfig",
        "MultiHubConfig",
        "TakeoverDamping",
    ]
    assert HubConfig.__module__ == "synapse_channel.core.hub_config"
    assert HubAuthConfig.__module__ == "synapse_channel.core.hub_config_auth"
    assert HubLimits.__module__ == "synapse_channel.core.hub_config_limits"
    assert TakeoverDamping.__module__ == "synapse_channel.core.hub_config_limits"
    assert HubMetricsConfig.__module__ == "synapse_channel.core.hub_config_metrics"
    assert HubLiveness.__module__ == "synapse_channel.core.hub_config_metrics"
    assert MultiHubConfig.__module__ == "synapse_channel.core.hub_config_routing"
    assert FederationConfig.__module__ == "synapse_channel.core.hub_config_routing"

    assert hub_config_module.HubConfig is HubConfig is synapse_channel.HubConfig
    assert hub_config_module.HubAuthConfig is HubAuthConfig is synapse_channel.HubAuthConfig
    assert hub_config_module.HubLimits is HubLimits is synapse_channel.HubLimits
    assert hub_config_module.TakeoverDamping is TakeoverDamping is synapse_channel.TakeoverDamping
    assert (
        hub_config_module.HubMetricsConfig is HubMetricsConfig is synapse_channel.HubMetricsConfig
    )
    assert hub_config_module.MultiHubConfig is MultiHubConfig is synapse_channel.MultiHubConfig
    assert (
        hub_config_module.FederationConfig is FederationConfig is synapse_channel.FederationConfig
    )
    assert hub_config_module.HubLiveness is HubLiveness
    assert not hasattr(synapse_channel, "HubLiveness")
