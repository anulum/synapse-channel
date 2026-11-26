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
import ctypes
import dataclasses
import importlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import typing
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel import federation_api

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


# Every Core symbol in the federation surface inventory, by module, with its kind:
# ``class``, ``callable`` (a function or a callable type alias) or ``value`` (a constant or
# a non-callable type alias). The list holds the fleet tier's consumed-symbol inventory as
# of its admission of Core 0.99.49 (with attachment fetch, local import, and record-keeping
# replacements) plus the operation record projector, read-only store reader, Fleet mirror
# contract, and owner-only path validation names that the fleet tier adopts in its
# retirement units. It pins existence and kind only, so a removal, a rename or a change
# between kinds fails here, in this repository's CI, before a release can break the
# consumer; the required fields and parameters of the most used primitives stay pinned above.
_FLEET_IMPORTS: dict[str, dict[str, str]] = {
    "synapse_channel.client.agent": {
        "SynapseAgent": "class",
    },
    "synapse_channel.core.attachment_store": {
        "AttachmentError": "class",
        "AttachmentStore": "class",
        "MAX_CHUNK_BYTES": "value",
    },
    "synapse_channel.core.attachment_transport": {
        "request_attachment": "callable",
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
    "synapse_channel.core.event_row_recovery": {
        "CorruptEventReason": "class",
        "CorruptEventRow": "class",
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
        "load_signing_key": "callable",
    },
    "synapse_channel.core.journal": {
        "EventKind": "class",
    },
    "synapse_channel.core.lifecycle": {
        "TaskStatus": "class",
    },
    "synapse_channel.core.merkle": {
        "MerkleRoot": "class",
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
    "synapse_channel.core.multihub_public_view": {
        "PUBLIC_VIEW_VERSION": "value",
        "is_withheld": "callable",
        "public_event_view": "callable",
    },
    "synapse_channel.core.multihub_transport": {
        "PING_INTERVAL": "value",
        "network_fetcher": "callable",
    },
    "synapse_channel.core.operation_record_reader": {
        "IndependentVerificationRecord": "class",
        "OPERATION_RECORD_KINDS": "value",
        "OperationAttemptSnapshot": "class",
        "OperationEvent": "class",
        "OperationEvidenceSnapshot": "class",
        "OperationMalformedRecord": "class",
        "OperationRecordReader": "class",
        "OperationRecordSnapshot": "class",
        "SCOPE_STOP_RECORD_KINDS": "value",
    },
    "synapse_channel.core.peer_identity": {
        "PeerRegistrationSigner": "class",
        "load_peer_registration_signer": "callable",
    },
    "synapse_channel.core.persistence": {
        "StoredEvent": "class",
        "StoredOperation": "class",
    },
    "synapse_channel.core.persistence_reader": {
        "EventLogReader": "class",
        "ReadableEventLog": "class",
    },
    "synapse_channel.core.persistence_sqlcipher": {
        "ReadOnlyOpenRefused": "class",
        "SqlCipherKeyError": "class",
        "SqlCipherUnavailableError": "class",
    },
    "synapse_channel.core.persistence_store_format": {
        "JournalInstance": "class",
        "STORE_FORMAT": "value",
        "StoreFormatRefused": "class",
    },
    "synapse_channel.core.protocol": {
        "MessageType": "class",
        "ProtocolNegotiation": "class",
    },
    "synapse_channel.core.receipt_signing": {
        "check_receipt_merkle_signature": "callable",
        "load_receipt_signing_key": "callable",
        "load_receipt_verification_key": "callable",
        "sign_merkle_commitment": "callable",
    },
    "synapse_channel.core.record_keeping": {
        "build_export": "callable",
        "export_to_json": "callable",
        "render_human": "callable",
        "utc_event_time": "callable",
    },
    "synapse_channel.core.record_keeping_source": {
        "DEFAULT_MAX_EVENTS": "value",
        "DEFAULT_MAX_PAYLOAD_BYTES": "value",
        "iter_record_source": "callable",
    },
    "synapse_channel.core.record_retention": {
        "assess_retention": "callable",
        "render_retention": "callable",
        "retention_to_json": "callable",
    },
    "synapse_channel.core.secure_path": {
        "PortableUserKey": "class",
        "SecurePathError": "class",
        "WindowsAce": "class",
        "assert_owner_only_descriptor": "callable",
        "assert_owner_only_file_path": "callable",
        "current_user_key": "callable",
        "evaluate_windows_owner_only_policy": "callable",
        "owner_only_floor_available": "callable",
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
    "synapse_channel.fleet_mirror_contract": {
        "MIRROR_CONTRACT_VERSION": "value",
        "MirrorVersionError": "class",
        "parse_mirror": "callable",
    },
    "synapse_channel.otel_export": {
        "DEFAULT_EXPORT_TIMEOUT": "value",
        "push_projection": "callable",
    },
}

_FLEET_SYMBOLS = 144
_FLEET_MODULES = 45


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


def test_field_names_accept_a_named_tuple_and_refuse_a_plain_object() -> None:
    """The record helper accepts a NamedTuple and rejects an object with no fields."""

    class Row(typing.NamedTuple):
        item: str

    assert _field_names(Row) == {"item"}
    with pytest.raises(AssertionError, match="neither a dataclass nor a NamedTuple"):
        _field_names(object())


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
    # the fleet tier's own inventory after it admitted Core 0.99.49.
    assert sum(len(symbols) for symbols in _FLEET_IMPORTS.values()) == _FLEET_SYMBOLS
    assert len(_FLEET_IMPORTS) == _FLEET_MODULES


def test_every_fleet_import_is_on_the_federation_interface_from_its_module() -> None:
    # The fleet tier imports these names through ``synapse_channel.federation_api``; the
    # inventory above says where each is defined, and the interface must agree exactly:
    # the same names, each its defining module's own object.
    inventory = {
        symbol: module_path for module_path, symbols in _FLEET_IMPORTS.items() for symbol in symbols
    }
    assert set(federation_api.__all__) == set(inventory)
    for symbol, module_path in inventory.items():
        defined = getattr(importlib.import_module(module_path), symbol)
        assert getattr(federation_api, symbol) is defined, f"{symbol} is not {module_path}'s"


def test_the_kind_of_a_symbol_is_class_callable_or_value() -> None:
    assert (_kind(int), _kind(len), _kind(7)) == ("class", "callable", "value")


_READ_ONLY_STORE_MODULES: tuple[str, ...] = (
    "synapse_channel.core.event_row_recovery",
    "synapse_channel.core.persistence",
    "synapse_channel.core.persistence_reader",
    "synapse_channel.core.persistence_sqlcipher",
    "synapse_channel.core.persistence_store_format",
)

_APPROVED_STORE_EXPORTS: frozenset[str] = frozenset(
    {
        "CorruptEventReason",
        "CorruptEventRow",
        "EventLogReader",
        "JournalInstance",
        "ReadOnlyOpenRefused",
        "ReadableEventLog",
        "STORE_FORMAT",
        "SqlCipherKeyError",
        "SqlCipherUnavailableError",
        "StoreFormatRefused",
        "StoredEvent",
        "StoredOperation",
    }
)


def _create_fresh_store(path: Path) -> object:
    """Writer-side test fixture helper: create a valid store with one event."""
    from synapse_channel.core.persistence import EventStore

    with EventStore(path) as writer:
        writer.append("chat", {"sender": "P/alice", "text": "hello"})
        return writer.journal_instance()


def _derived_connection_denylist() -> dict[str, object]:
    """Derive public connection-taking or connect_* callables across the five store modules."""
    denied: dict[str, object] = {}
    for module_path in _READ_ONLY_STORE_MODULES:
        module = importlib.import_module(module_path)
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or obj.__module__ != module_path:
                continue
            params = set(inspect.signature(obj).parameters)
            if name.startswith("connect_") or bool(params & {"conn", "connection"}):
                denied[name] = obj
    return denied


def test_read_only_store_exports_stay_within_allowlist_and_off_raw_connections(
    tmp_path: Path,
) -> None:
    # (a) Allowlist: the federation surface exposes exactly the 12 approved names from the
    # five store modules (11 read-only store reader symbols plus StoredEvent).
    inventory_store_names = {
        symbol
        for module_path in _READ_ONLY_STORE_MODULES
        for symbol in _FLEET_IMPORTS.get(module_path, {})
    }
    assert inventory_store_names == _APPROVED_STORE_EXPORTS

    exported_objects = {name: getattr(federation_api, name) for name in federation_api.__all__}
    live_store_names = {
        name
        for name, obj in exported_objects.items()
        if getattr(obj, "__module__", None) in _READ_ONLY_STORE_MODULES
    }
    live_store_names |= {name for name in federation_api.__all__ if name in inventory_store_names}
    assert live_store_names == _APPROVED_STORE_EXPORTS

    # (b) Derived denylist: no connect_* or conn/connection-taking function is reachable.
    denied = _derived_connection_denylist()
    assert {"read_journal_instance", "stored_store_format", "connect_event_store"} <= set(denied)
    for name, fn in denied.items():
        assert not hasattr(federation_api, name)
        assert name not in federation_api.__all__
        assert all(obj is not fn for obj in exported_objects.values())

    # (c) Live reader instance and exported store classes expose no raw connection.
    db = tmp_path / "live.db"
    _create_fresh_store(db)
    with federation_api.EventLogReader(db) as reader:
        public_attrs = {
            name for name in (*vars(reader), *dir(type(reader))) if not name.startswith("_")
        }
        for attr in public_attrs:
            assert not isinstance(getattr(reader, attr), sqlite3.Connection), (
                f"EventLogReader.{attr} exposed a raw sqlite3.Connection"
            )

    for name in _APPROVED_STORE_EXPORTS:
        obj = getattr(federation_api, name)
        if not isinstance(obj, type):
            continue
        for member_name in dir(obj):
            if member_name.startswith("_"):
                continue
            member = getattr(obj, member_name)
            if inspect.isfunction(member) or inspect.ismethod(member):
                params = set(inspect.signature(member).parameters)
                assert not (params & {"conn", "connection"}), (
                    f"{name}.{member_name} accepts a raw connection parameter"
                )


def test_read_only_store_refusals_through_federation_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Missing path inside a missing subdirectory creates nothing.
    missing_dir = tmp_path / "missing_subdir"
    missing_db = missing_dir / "events.db"
    with pytest.raises(federation_api.ReadOnlyOpenRefused):
        federation_api.EventLogReader(missing_db)
    assert not missing_dir.exists()
    assert not missing_db.exists()
    assert not Path(f"{missing_db}-wal").exists()
    assert not Path(f"{missing_db}-shm").exists()

    # 2. Existing empty SQLite file without an events table is refused unchanged.
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(empty_db).close()
    empty_bytes = empty_db.read_bytes()
    empty_mtime = empty_db.stat().st_mtime_ns
    with pytest.raises(federation_api.ReadOnlyOpenRefused):
        federation_api.EventLogReader(empty_db)
    assert empty_db.read_bytes() == empty_bytes
    assert empty_db.stat().st_mtime_ns == empty_mtime

    # 3. Non-SQLite junk file opened without a key raises SqlCipherKeyError.
    junk_db = tmp_path / "junk.db"
    junk_db.write_bytes(b"not-a-sqlite-header" * 16)
    with pytest.raises(federation_api.SqlCipherKeyError):
        federation_api.EventLogReader(junk_db)

    # 4. Keyed open without an importable SQLCipher driver raises SqlCipherUnavailableError.
    existing_db = tmp_path / "existing.db"
    _create_fresh_store(existing_db)
    monkeypatch.setitem(sys.modules, "sqlcipher3", None)
    monkeypatch.setitem(sys.modules, "pysqlcipher3", None)
    with pytest.raises(federation_api.SqlCipherUnavailableError):
        federation_api.EventLogReader(existing_db, key=bytes(32))

    # 5. A store whose format floor exceeds STORE_FORMAT is refused unchanged.
    future_db = tmp_path / "future.db"
    _create_fresh_store(future_db)
    future_floor = federation_api.STORE_FORMAT + 1
    conn = sqlite3.connect(future_db)
    try:
        conn.execute(
            "UPDATE store_format SET floor = ?, floor_release = ? WHERE id = 1",
            (future_floor, "9.0.0"),
        )
        conn.execute(f"PRAGMA user_version = {future_floor}")
        conn.commit()
    finally:
        conn.close()
    future_bytes = future_db.read_bytes()
    with pytest.raises(federation_api.StoreFormatRefused) as refused:
        federation_api.EventLogReader(future_db)
    assert refused.value.floor == future_floor
    assert future_db.read_bytes() == future_bytes


def test_read_only_store_reads_and_static_protocol_through_federation_api(tmp_path: Path) -> None:
    from synapse_channel.core.persistence import EventStore

    db = tmp_path / "reads.db"
    with EventStore(db) as writer:
        writer.commit_operation(
            operation_key="op-key-1",
            request_digest="a" * 64,
            response={"ok": True},
            events=[("chat", {"sender": "P/alice", "text": "committed"})],
            intent={"kind": "test"},
        )
        corrupt_seq = writer.append("chat", {"sender": "P/alice", "text": "will-corrupt"})
        expected_instance = writer.journal_instance()

    # Corrupt the second row's JSON payload directly via sqlite3 in test setup.
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE events SET payload = ? WHERE seq = ?", ("{not-json", corrupt_seq))
        conn.commit()
    finally:
        conn.close()

    with federation_api.EventLogReader(db) as reader:
        # 6. journal_instance() matches EventStore.journal_instance() and has exported type.
        actual_instance = reader.journal_instance()
        assert actual_instance == expected_instance
        assert isinstance(actual_instance, federation_api.JournalInstance)

        # 7. get_operation() returns StoredOperation; corrupt_rows() returns CorruptEventRow
        #    whose reasons are CorruptEventReason members.
        stored_op = reader.get_operation("op-key-1")
        assert isinstance(stored_op, federation_api.StoredOperation)
        assert stored_op.operation_key == "op-key-1"

        corrupt = reader.corrupt_rows()
        assert len(corrupt) == 1
        assert isinstance(corrupt[0], federation_api.CorruptEventRow)
        assert corrupt[0].seq == corrupt_seq
        assert corrupt[0].reasons
        assert all(
            isinstance(reason, federation_api.CorruptEventReason) for reason in corrupt[0].reasons
        )

    # 8. Strict mypy probe: every new name is usable from federation_api alone, and both
    #    EventLogReader and EventStore satisfy federation_api.ReadableEventLog.
    consumer = tmp_path / "records_consumer.py"
    consumer.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "from synapse_channel.core.persistence import EventStore",
                "from synapse_channel.federation_api import (",
                "    STORE_FORMAT,",
                "    CorruptEventReason,",
                "    CorruptEventRow,",
                "    EventLogReader,",
                "    JournalInstance,",
                "    ReadableEventLog,",
                "    ReadOnlyOpenRefused,",
                "    SqlCipherKeyError,",
                "    SqlCipherUnavailableError,",
                "    StoredEvent,",
                "    StoredOperation,",
                "    StoreFormatRefused,",
                ")",
                "",
                "def check(",
                "    path: Path,",
                "    writer: EventStore,",
                ") -> tuple[",
                "    int, JournalInstance | None, StoredOperation | None, list[StoredEvent]",
                "]:",
                "    writer_as_log: ReadableEventLog = writer",
                "    _ = writer_as_log.max_seq()",
                "    writer_inst: JournalInstance | None = writer_as_log.journal_instance()",
                "    _ = writer_inst",
                "    try:",
                "        reader = EventLogReader(path)",
                "    except (",
                "        ReadOnlyOpenRefused,",
                "        StoreFormatRefused,",
                "        SqlCipherKeyError,",
                "        SqlCipherUnavailableError,",
                "    ):",
                "        return STORE_FORMAT, None, None, []",
                "    reader_as_log: ReadableEventLog = reader",
                "    rows: tuple[CorruptEventRow, ...] = reader.corrupt_rows()",
                "    reasons: tuple[CorruptEventReason, ...] = rows[0].reasons if rows else ()",
                "    _ = reasons",
                "    events = reader_as_log.read_since(0)",
                "    return (",
                "        STORE_FORMAT,",
                "        reader_as_log.journal_instance(),",
                "        reader.get_operation('k'),",
                "        events,",
                "    )",
                "",
            )
        ),
        encoding="utf-8",
    )
    package_root = Path(synapse_channel.__file__).resolve().parents[1]
    probe = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        check=False,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        text=True,
        timeout=180,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_mirror_contract_exports_boundary_parity_and_strict_mypy_consumer(tmp_path: Path) -> None:
    """Pin the 3-symbol mirror contract export boundary, v1 behaviour, and strict mypy use."""
    import synapse_channel.fleet_mirror_contract as mirror_mod

    exported = set(federation_api.__all__)
    expected = {"MIRROR_CONTRACT_VERSION", "MirrorVersionError", "parse_mirror"}
    mirror_exports = {
        name
        for name in exported
        if name in vars(mirror_mod) and getattr(federation_api, name) is getattr(mirror_mod, name)
    }
    assert mirror_exports == expected
    assert "MAX_MIRROR_BYTES" not in exported
    assert not hasattr(federation_api, "MAX_MIRROR_BYTES")
    assert not hasattr(federation_api, "parse_mirror_document")

    assert type(federation_api.MIRROR_CONTRACT_VERSION) is int
    assert federation_api.MIRROR_CONTRACT_VERSION == 1
    assert federation_api.MirrorVersionError is mirror_mod.MirrorVersionError
    assert federation_api.parse_mirror is mirror_mod.parse_mirror

    base_doc: dict[str, object] = {
        "version": federation_api.MIRROR_CONTRACT_VERSION,
        "source_id": "fleet-a",
        "exported_at": 1_700_000_000.0,
        "advisory": True,
        "snapshot": {
            "advisory": "read-only advisory view",
            "generated_at": 1_700_000_000.0,
            "peers": [],
            "tasks": [],
            "progress_notes": 0,
        },
    }
    parsed = federation_api.parse_mirror(json.dumps(base_doc))
    assert parsed == base_doc

    generation = {"version": 1, "device": 17, "inode": 99, "marker": "a" * 32}
    with_gen = {
        **base_doc,
        "writer_generation": generation,
        "snapshot": {
            **typing.cast(dict[str, object], base_doc["snapshot"]),
            "writer_generation": generation,
        },
    }
    assert federation_api.parse_mirror(json.dumps(with_gen)) == with_gen

    # Unsupported versions raise MirrorVersionError (also a ValueError with code='mirror_version').
    for bad_version in (0, 2, True, "1"):
        bad = {**base_doc, "version": bad_version}
        with pytest.raises(
            federation_api.MirrorVersionError, match="unsupported mirror version"
        ) as exc_info:
            federation_api.parse_mirror(json.dumps(bad))
        assert isinstance(exc_info.value, ValueError)
        assert exc_info.value.code == "mirror_version"

    # Malformed v1 shape raises plain ValueError, not MirrorVersionError.
    mismatched_gen = {**base_doc, "writer_generation": generation}
    with pytest.raises(ValueError, match="both envelope and snapshot") as val_exc:
        federation_api.parse_mirror(json.dumps(mismatched_gen))
    assert not isinstance(val_exc.value, federation_api.MirrorVersionError)

    # Strict mypy probe importing only through federation_api.
    consumer = tmp_path / "mirror_consumer.py"
    consumer.write_text(
        "\n".join(
            (
                "from typing import Any",
                "from synapse_channel.federation_api import (",
                "    MIRROR_CONTRACT_VERSION,",
                "    MirrorVersionError,",
                "    parse_mirror,",
                ")",
                "",
                "def check_export(raw: str) -> dict[str, Any] | None:",
                "    expected: int = MIRROR_CONTRACT_VERSION",
                "    try:",
                "        doc = parse_mirror(raw)",
                "    except MirrorVersionError:",
                "        return None",
                "    assert doc['version'] == expected",
                "    return doc",
                "",
            )
        ),
        encoding="utf-8",
    )
    package_root = Path(synapse_channel.__file__).resolve().parents[1]
    probe = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        check=False,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        text=True,
        timeout=180,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


_SECURITY_MODULES: tuple[str, ...] = (
    "synapse_channel.core.secure_path",
    "synapse_channel.core.secure_path_windows",
)

_APPROVED_SECURITY_EXPORTS: frozenset[str] = frozenset(
    {
        "PortableUserKey",
        "SecurePathError",
        "WindowsAce",
        "assert_owner_only_descriptor",
        "assert_owner_only_file_path",
        "current_user_key",
        "evaluate_windows_owner_only_policy",
        "owner_only_floor_available",
    }
)


def _derived_security_creator_denylist() -> dict[str, object]:
    """Derive public create_*, apply_*, or *birth* callables across both secure-path modules."""
    denied: dict[str, object] = {}
    for module_path in _SECURITY_MODULES:
        module = importlib.import_module(module_path)
        for name, obj in inspect.getmembers(module, callable):
            if name.startswith("_") or getattr(obj, "__module__", None) != module_path:
                continue
            if name.startswith(("create_", "apply_", "windows_create_", "windows_apply_")) or (
                "birth" in name
            ):
                denied[name] = obj
    return denied


def _is_ctypes_type(candidate: object) -> bool:
    """Return whether ``candidate`` is a ``ctypes`` type or annotation mentioning ``ctypes``."""
    if isinstance(candidate, str):
        return "ctypes" in candidate or "wintypes" in candidate
    if isinstance(candidate, type):
        module_name = getattr(candidate, "__module__", "")
        if module_name == "ctypes" or module_name.startswith("ctypes."):
            return True
        if issubclass(candidate, (ctypes.Structure, ctypes.Union, ctypes.Array)):
            return True
    origin = typing.get_origin(candidate)
    if origin is not None and _is_ctypes_type(origin):
        return True
    return any(_is_ctypes_type(arg) for arg in typing.get_args(candidate))


def test_security_exports_stay_within_allowlist_and_off_creators_and_ctypes() -> None:
    # (a) Exact allowlist: 8 approved names from core.secure_path, 0 from core.secure_path_windows.
    inventory_sec_names = {
        symbol
        for module_path in _SECURITY_MODULES
        for symbol in _FLEET_IMPORTS.get(module_path, {})
    }
    assert inventory_sec_names == _APPROVED_SECURITY_EXPORTS
    assert "synapse_channel.core.secure_path_windows" not in _FLEET_IMPORTS

    exported_objects = {name: getattr(federation_api, name) for name in federation_api.__all__}
    live_sec_names = {
        name
        for name, obj in exported_objects.items()
        if getattr(obj, "__module__", None) == "synapse_channel.core.secure_path"
    }
    live_sec_names |= {name for name in federation_api.__all__ if name in inventory_sec_names}
    assert live_sec_names == _APPROVED_SECURITY_EXPORTS

    live_win_names = {
        name
        for name, obj in exported_objects.items()
        if getattr(obj, "__module__", None) == "synapse_channel.core.secure_path_windows"
    }
    assert live_win_names == set()

    # (b) Derived denylist: creator/apply/birth helpers are non-empty and unreachable.
    denied = _derived_security_creator_denylist()
    assert {
        "apply_owner_only_dir",
        "apply_owner_only_file",
        "create_owner_only_file",
        "owner_birth_sddl",
        "owner_birth_security_descriptor",
    } <= set(denied)
    for name, fn in denied.items():
        assert not hasattr(federation_api, name)
        assert name not in federation_api.__all__
        assert all(obj is not fn for obj in exported_objects.values())

    # (c) Zero ctypes types exported or referenced in any exported annotation.
    for name, obj in exported_objects.items():
        assert not _is_ctypes_type(obj), f"federation_api.{name} leaked a ctypes type"
        assert not _is_ctypes_type(type(obj)), f"federation_api.{name} leaked a ctypes value"
        if inspect.isfunction(obj):
            for param_name, ann in obj.__annotations__.items():
                assert not _is_ctypes_type(ann), (
                    f"federation_api.{name} annotation {param_name!r} leaked ctypes: {ann!r}"
                )
        elif isinstance(obj, type):
            for field_name, ann in getattr(obj, "__annotations__", {}).items():
                assert not _is_ctypes_type(ann), (
                    f"federation_api.{name}.{field_name} leaked ctypes: {ann!r}"
                )
            for member_name in dir(obj):
                if member_name.startswith("_") and member_name != "__init__":
                    continue
                member = getattr(obj, member_name)
                if inspect.isfunction(member) or inspect.ismethod(member):
                    for param_name, ann in getattr(member, "__annotations__", {}).items():
                        assert not _is_ctypes_type(ann), (
                            f"federation_api.{name}.{member_name} annotation {param_name!r} "
                            f"leaked ctypes: {ann!r}"
                        )


def test_owner_only_path_validation_through_federation_api(tmp_path: Path) -> None:
    assert federation_api.owner_only_floor_available() is (os.name in ("posix", "nt"))
    identity = federation_api.current_user_key()
    assert isinstance(identity, federation_api.PortableUserKey)
    assert identity.value

    # Pure Windows owner-only policy through federation_api names only.
    sample_path = Path("C:/private/key")
    sid = "S-1-5-21-100"
    federation_api.evaluate_windows_owner_only_policy(
        path=sample_path,
        purpose="consumer",
        owner_sid=sid,
        current_sid=sid,
        dacl_present=True,
        aces=(
            federation_api.WindowsAce(ace_type=0, mask=0x001F01FF, sid=sid),
            federation_api.WindowsAce(ace_type=0, mask=0x001F01FF, sid="S-1-5-18"),
            federation_api.WindowsAce(ace_type=1, mask=0x001F01FF, sid="S-1-1-0"),
        ),
    )
    for bad_ace_type in (0x5, 0x9, 0x2):
        with pytest.raises(federation_api.SecurePathError, match="unsupported ACE type"):
            federation_api.evaluate_windows_owner_only_policy(
                path=sample_path,
                purpose="consumer",
                owner_sid=sid,
                current_sid=sid,
                dacl_present=True,
                aces=(
                    federation_api.WindowsAce(ace_type=bad_ace_type, mask=0x120089, sid="S-1-1-0"),
                ),
            )
    with pytest.raises(federation_api.SecurePathError, match="accessible by other principals"):
        federation_api.evaluate_windows_owner_only_policy(
            path=sample_path,
            purpose="consumer",
            owner_sid=sid,
            current_sid=sid,
            dacl_present=True,
            aces=(federation_api.WindowsAce(ace_type=0, mask=0x00120089, sid="S-1-1-0"),),
        )

    if os.name == "posix":
        secret = tmp_path / "consumer.secret"
        secret.write_bytes(b"token")
        secret.chmod(0o600)
        federation_api.assert_owner_only_file_path(
            secret, purpose="consumer", require_single_link=True
        )
        fd = os.open(secret, os.O_RDONLY)
        try:
            federation_api.assert_owner_only_descriptor(
                fd, path=secret, purpose="consumer", require_single_link=True
            )
        finally:
            os.close(fd)
        secret.chmod(0o644)
        with pytest.raises(federation_api.SecurePathError, match="accessible by other users"):
            federation_api.assert_owner_only_file_path(secret, purpose="consumer")

    # Strict mypy probe: all 8 security names usable from federation_api alone.
    consumer = tmp_path / "security_consumer.py"
    consumer.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                "from synapse_channel.federation_api import (",
                "    PortableUserKey,",
                "    SecurePathError,",
                "    WindowsAce,",
                "    assert_owner_only_descriptor,",
                "    assert_owner_only_file_path,",
                "    current_user_key,",
                "    evaluate_windows_owner_only_policy,",
                "    owner_only_floor_available,",
                ")",
                "",
                "def verify(path: Path, fd: int) -> PortableUserKey | None:",
                "    if not owner_only_floor_available():",
                "        return None",
                "    key: PortableUserKey = current_user_key()",
                "    try:",
                "        assert_owner_only_file_path(path, purpose='k', require_single_link=True)",
                "        assert_owner_only_descriptor(fd, path=path, purpose='k')",
                "        evaluate_windows_owner_only_policy(",
                "            path=path,",
                "            purpose='k',",
                "            owner_sid=key.value,",
                "            current_sid=key.value,",
                "            dacl_present=True,",
                "            aces=(WindowsAce(ace_type=0, mask=0x001F01FF, sid=key.value),),",
                "        )",
                "    except SecurePathError:",
                "        return None",
                "    return key",
                "",
            )
        ),
        encoding="utf-8",
    )
    package_root = Path(synapse_channel.__file__).resolve().parents[1]
    probe = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        check=False,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        text=True,
        timeout=180,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
