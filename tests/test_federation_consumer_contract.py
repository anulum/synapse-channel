# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — contract tests for the federation surface out-of-tree consumers build on
"""Pin the core federation/multihub surface that out-of-tree consumers depend on.

The open-source core provides the federation primitives — the multihub follower and
its event-fetcher type, claim forwarding, the persisted event shape, the federation
store and its peer records, and the TLS pin helper — and out-of-tree consumers (the
commercial fleet tier operates them at production scale) import these directly from
``synapse_channel.core.*`` rather than through the top-level package surface that
``test_public_api`` guards. Those deep imports are therefore an unadvertised but real
API contract: a rename, a moved module, a dropped field, or a removed parameter here
breaks a downstream consumer silently, and nothing else in this repository would catch
it before release.

These tests make that contract explicit and enforced. Each primitive is pinned by its
module path, its kind (record, callable, callable-type-alias, class, exception, or
module), and the members a consumer relies on — required fields for a record, required
parameters for a callable. Backward-compatible additions (a new optional field or
keyword-only parameter) pass; a removal or rename fails. The set is also counted, so a
primitive cannot be quietly dropped from the contract. This is the surface the 1.0.0
wire/API freeze must lock, and the guard that keeps the pre-1.0 window from breaking it
by accident.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import importlib
import inspect
import typing

import pytest

# Record primitives (dataclass or NamedTuple) → the field names a consumer reads.
# Required subset only, so a backward-compatible new field does not fail the contract.
_RECORD_CONTRACT: dict[tuple[str, str], frozenset[str]] = {
    ("synapse_channel.core.persistence", "StoredEvent"): frozenset(
        {"seq", "ts", "kind", "payload"}
    ),
    ("synapse_channel.core.multihub_claim_wire", "ClaimForwardRequest"): frozenset(
        {"namespace", "claimant", "task_id", "claim"}
    ),
    ("synapse_channel.core.multihub_claim_wire", "ClaimForwardResult"): frozenset(
        {"granted", "task_id", "namespace", "owner_hub_id"}
    ),
    ("synapse_channel.core.federation_store", "FederationRecord"): frozenset(
        {"peer", "provenance"}
    ),
    ("synapse_channel.core.federation", "FederationPeer"): frozenset(
        {"domain_id", "namespaces", "certificate_pins", "signing_key_ids"}
    ),
}

# Callable primitives → the parameter names a consumer passes. Required subset only.
_CALLABLE_CONTRACT: dict[tuple[str, str], frozenset[str]] = {
    ("synapse_channel.core.multihub_transport", "network_fetcher"): frozenset({"uri", "local_id"}),
    ("synapse_channel.core.federation_store", "load_store"): frozenset({"path"}),
    ("synapse_channel.core.federation_store", "bundle_from_store"): frozenset({"path"}),
    ("synapse_channel.core.tls", "certificate_sha256_pin"): frozenset({"certfile"}),
}

# Callable-type-alias primitives → names a consumer annotates with (e.g. the fetcher type).
_CALLABLE_ALIAS_CONTRACT: tuple[tuple[str, str], ...] = (
    ("synapse_channel.core.multihub_follower", "EventFetcher"),
)

# Class primitives → importable classes a consumer subclasses or type-checks against.
_CLASS_CONTRACT: tuple[tuple[str, str], ...] = (
    ("synapse_channel.core.multihub_follower", "MultiHubFollower"),
)

# Exception primitives → error types a consumer catches.
_EXCEPTION_CONTRACT: tuple[tuple[str, str], ...] = (
    ("synapse_channel.core.multihub_claim_transport", "ClaimForwardError"),
)

# Module primitives → importable modules a consumer reaches into.
_MODULE_CONTRACT: tuple[str, ...] = ("synapse_channel.core.multihub_federation",)

# The full contract size — a primitive cannot be silently dropped without failing this.
_CONTRACT_SIZE = (
    len(_RECORD_CONTRACT)
    + len(_CALLABLE_CONTRACT)
    + len(_CALLABLE_ALIAS_CONTRACT)
    + len(_CLASS_CONTRACT)
    + len(_EXCEPTION_CONTRACT)
    + len(_MODULE_CONTRACT)
)


# Every Core symbol the fleet tier imports, by module, with its kind: ``class``, ``callable``
# (a function or a callable type alias) or ``value`` (a constant or a non-callable type
# alias). The list is the fleet tier's consumed-symbol inventory at Fleet 0.11.0. It pins
# existence and kind only, so a removal, a rename or a change between kinds fails here,
# in this repository's CI, before a release can break the consumer; the required fields
# and parameters of the most used primitives stay pinned above.
_FLEET_IMPORTS: dict[str, dict[str, str]] = {
    "synapse_channel.core.at_rest": {
        "load_key_file": "callable",
    },
    "synapse_channel.core.causality": {
        "DEFAULT_MAX_GRAPH_NODES": "value",
        "GRAPH_KINDS": "value",
        "LIFECYCLE": "value",
    },
    "synapse_channel.core.causality_federation": {
        "FEDERATION": "value",
        "FederatedCausalGraph": "class",
        "FederatedNode": "class",
        "HubEventRef": "class",
        "build_federated_graph": "callable",
    },
    "synapse_channel.core.causality_otel": {
        "OtelProjection": "class",
        "OtelSpanRecord": "class",
        "SERVICE_NAME": "value",
        "SPAN_ID_HEX_LENGTH": "value",
        "SPAN_STATUS_ERROR": "value",
        "SPAN_STATUS_UNSET": "value",
        "SpanAttributeValue": "value",
        "SpanLinkRecord": "class",
        "projection_to_json": "callable",
        "span_id_for_root": "callable",
        "trace_id_for_task": "callable",
    },
    "synapse_channel.core.clock_skew": {
        "ClockSkew": "class",
    },
    "synapse_channel.core.correlation_contract": {
        "CONTRACT_RESOURCE_ATTRIBUTE": "value",
        "CORRELATION_CONTRACT": "value",
        "CORRELATION_SCHEMA_URL": "value",
    },
    "synapse_channel.core.correlation_points": {
        "CORRELATION_KINDS": "value",
        "CorrelationPoint": "class",
        "correlation_points": "callable",
    },
    "synapse_channel.core.entitlement_advert": {
        "EntitlementAdvertError": "class",
        "validate_advert": "callable",
    },
    "synapse_channel.core.event_row_mac": {
        "RowMacKey": "class",
        "load_or_create_row_mac_key": "callable",
        "row_mac_key_path": "callable",
    },
    "synapse_channel.core.federation": {
        "FederationBundle": "class",
        "FederationPeer": "class",
    },
    "synapse_channel.core.federation_store": {
        "FederationRecord": "class",
        "STORE_VERSION": "value",
        "load_store": "callable",
        "peer_from_dict": "callable",
        "save_store": "callable",
    },
    "synapse_channel.core.fleet_health": {
        "build_local_fleet_health": "callable",
    },
    "synapse_channel.core.identity_keys": {
        "IdentityKeyError": "class",
    },
    "synapse_channel.core.journal": {
        "EventKind": "class",
    },
    "synapse_channel.core.lifecycle": {
        "TaskStatus": "class",
    },
    "synapse_channel.core.merkle": {
        "MerkleRoot": "class",
        "RunningRoot": "class",
        "build_root": "callable",
        "render_root_markdown": "callable",
        "root_to_json": "callable",
        "run_root": "callable",
        "verify_root": "callable",
    },
    "synapse_channel.core.multihub_claim_transport": {
        "ClaimForwardError": "class",
        "DEFAULT_FORWARD_TIMEOUT": "value",
        "forward_claim": "callable",
    },
    "synapse_channel.core.multihub_claim_wire": {
        "ClaimForwardRequest": "class",
        "ClaimForwardResult": "class",
        "ClaimWireError": "class",
        "encode_claim_forward_request": "callable",
    },
    "synapse_channel.core.multihub_federation": {
        "MultiHubAuthorisation": "class",
        "MultiHubAuthoriser": "callable",
        "authorise_multihub_peer": "callable",
    },
    "synapse_channel.core.multihub_fold": {
        "ObservedBoardConflict": "class",
        "ObservedBoardProvenance": "class",
        "ObservedState": "class",
        "fold_observed_state": "callable",
    },
    "synapse_channel.core.multihub_follower": {
        "EventFetcher": "callable",
    },
    "synapse_channel.core.multihub_merge": {
        "HubEvent": "class",
        "merge_event_logs": "callable",
        "tag_events": "callable",
    },
    "synapse_channel.core.multihub_transport": {
        "PING_INTERVAL": "value",
        "network_fetcher": "callable",
    },
    "synapse_channel.core.peer_identity": {
        "PeerRegistrationSigner": "class",
        "load_peer_registration_signer": "callable",
    },
    "synapse_channel.core.persistence": {
        "StoredEvent": "class",
    },
    "synapse_channel.core.persistence_sqlcipher": {
        "apply_sqlcipher_key": "callable",
        "import_sqlcipher_module": "callable",
    },
    "synapse_channel.core.protocol": {
        "ProtocolNegotiation": "class",
        "WIRE_PROTOCOL_VERSION": "value",
    },
    "synapse_channel.core.receipt_signing": {
        "check_receipt_merkle_signature": "callable",
        "load_receipt_signing_key": "callable",
        "load_receipt_verification_key": "callable",
        "sign_merkle_commitment": "callable",
    },
    "synapse_channel.core.spend_epoch": {
        "OwnerRevocation": "class",
        "SpendEpochError": "class",
        "usable_grant": "callable",
        "verify_owner_revocation": "callable",
    },
    "synapse_channel.core.spend_transport": {
        "Connector": "callable",
        "DEFAULT_SPEND_TIMEOUT": "value",
        "SpendTransportError": "class",
        "SpendTransportTimeoutError": "class",
        "request_spend": "callable",
    },
    "synapse_channel.core.task_causality": {
        "TASK_CAUSAL_PARENT_FIELD": "value",
        "parse_task_causal_parent": "callable",
    },
    "synapse_channel.core.tls": {
        "MTLSPeerTrustBundle": "class",
        "MTLSTrustedPeer": "class",
        "certificate_sha256_pin": "callable",
        "certificate_sha256_pin_from_der": "callable",
    },
    "synapse_channel.otel_export": {
        "DEFAULT_EXPORT_TIMEOUT": "value",
        "push_projection": "callable",
    },
}

_FLEET_SYMBOLS = 98
_FLEET_MODULES = 33


def _kind(obj: object) -> str:
    """Return ``class``, ``callable`` or ``value`` for one imported symbol."""
    if isinstance(obj, type):
        return "class"
    return "callable" if callable(obj) else "value"


def _resolve(module_path: str, symbol: str) -> object:
    """Import ``module_path`` and return its ``symbol`` (fails clearly if absent)."""
    module = importlib.import_module(module_path)
    assert hasattr(module, symbol), f"{module_path}.{symbol} is gone — a consumer contract break"
    return getattr(module, symbol)


def _field_names(obj: object) -> set[str]:
    """Return the field names of a dataclass or a NamedTuple, else fail."""
    if dataclasses.is_dataclass(obj):
        return {field.name for field in dataclasses.fields(obj)}
    named_tuple_fields = getattr(obj, "_fields", None)
    if named_tuple_fields is not None:
        return set(named_tuple_fields)
    raise AssertionError(f"{obj!r} is neither a dataclass nor a NamedTuple")


@pytest.mark.parametrize(("location", "required_fields"), _RECORD_CONTRACT.items())
def test_record_primitives_keep_their_required_fields(
    location: tuple[str, str], required_fields: frozenset[str]
) -> None:
    present = _field_names(_resolve(*location))
    missing = required_fields - present
    assert not missing, f"{location[1]} dropped consumer fields {missing}"


@pytest.mark.parametrize(("location", "required_params"), _CALLABLE_CONTRACT.items())
def test_callable_primitives_keep_their_required_parameters(
    location: tuple[str, str], required_params: frozenset[str]
) -> None:
    obj = _resolve(*location)
    assert callable(obj), f"{location[1]} is no longer callable"
    params = set(inspect.signature(obj).parameters)
    missing = required_params - params
    assert not missing, f"{location[1]} dropped consumer parameters {missing}"


@pytest.mark.parametrize("location", _CALLABLE_ALIAS_CONTRACT)
def test_callable_alias_primitives_stay_callable_aliases(location: tuple[str, str]) -> None:
    obj = _resolve(*location)
    assert typing.get_origin(obj) is collections.abc.Callable, (
        f"{location[1]} is no longer the callable type alias a consumer annotates with"
    )


@pytest.mark.parametrize("location", _CLASS_CONTRACT)
def test_class_primitives_are_importable_classes(location: tuple[str, str]) -> None:
    obj = _resolve(*location)
    assert isinstance(obj, type), f"{location[1]} is no longer a class a consumer can build on"


@pytest.mark.parametrize("location", _EXCEPTION_CONTRACT)
def test_exception_primitives_stay_exceptions(location: tuple[str, str]) -> None:
    obj = _resolve(*location)
    assert isinstance(obj, type) and issubclass(obj, Exception), (
        f"{location[1]} is no longer an exception a consumer can catch"
    )


@pytest.mark.parametrize("module_path", _MODULE_CONTRACT)
def test_module_primitives_import(module_path: str) -> None:
    assert importlib.import_module(module_path) is not None


def test_contract_covers_the_full_consumer_surface() -> None:
    # A guard against silently shrinking the contract: the thirteen federation primitives
    # verified against the current core stay accounted for, so dropping one is a
    # deliberate, reviewed edit here rather than an unnoticed regression.
    assert _CONTRACT_SIZE == 13


@pytest.mark.parametrize(
    ("module_path", "symbol", "kind"),
    [
        (module_path, symbol, kind)
        for module_path, symbols in _FLEET_IMPORTS.items()
        for symbol, kind in symbols.items()
    ],
)
def test_every_symbol_the_fleet_tier_imports_keeps_its_name_and_kind(
    module_path: str, symbol: str, kind: str
) -> None:
    assert _kind(_resolve(module_path, symbol)) == kind, (
        f"{module_path}.{symbol} is no longer a {kind}; the fleet tier imports it as one"
    )


def test_the_fleet_import_inventory_is_complete() -> None:
    # Dropping a symbol from the inventory is a deliberate, reviewed edit: the counts are
    # the fleet tier's own inventory at Fleet 0.11.0.
    assert sum(len(symbols) for symbols in _FLEET_IMPORTS.values()) == _FLEET_SYMBOLS
    assert len(_FLEET_IMPORTS) == _FLEET_MODULES


def test_the_kind_of_a_symbol_is_class_callable_or_value() -> None:
    assert (_kind(int), _kind(len), _kind(7)) == ("class", "callable", "value")
