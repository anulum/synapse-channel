# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the one stable import surface for out-of-tree federation consumers
"""Import the Core names an out-of-tree federation consumer may rely on, from one place.

A consumer that follows peer hubs, forwards claims, shares spend, transfers attachments
or exports evidence imports from this package, not from Core's internal modules. Every
name here is the defining module's own object (``federation_api.X is <defining
module>.X``), so ``isinstance``, ``except`` and equality keep their meaning; Core may
move a name between internal modules without changing this surface.

Importing this package loads nothing else: a name is imported, with its capability, on
first use. Type checkers see the real types. Removing or renaming a name is a reviewed
change of ``tests/test_federation_api.py``, of the fleet inventory guard and of the API
stability page. The private submodules only group the names by capability:

- Following and merging peer hubs: ``federation``, ``federation_store``,
  ``multihub_federation``, ``multihub_follower``, ``multihub_transport``,
  ``multihub_merge``, ``multihub_fold``, ``persistence``, ``clock_skew``.
- Claim forwarding: ``multihub_claim_transport``, ``multihub_claim_wire``.
- Shared spend: ``spend_epoch``, ``spend_transport``.
- Attachments: ``attachment_store``, ``attachment_transport``.
- Evidence, causality and OpenTelemetry: ``journal``, ``lifecycle``, ``record_keeping``,
  ``task_causality``, ``causality``, ``causality_federation``, ``causality_otel``,
  ``correlation_contract``, ``correlation_points``, ``otel_export``.
- Receipts and integrity: ``merkle``, ``receipt_signing``, ``event_row_mac``.
- Identity, TLS and keys at rest: ``identity_keys``, ``peer_identity``, ``tls``,
  ``at_rest``, ``persistence_sqlcipher``.
- Health and entitlement: ``fleet_health``, ``entitlement_advert``.
- Protocol and client: ``protocol``, ``client.agent``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.federation_api._attachments import (
        MAX_CHUNK_BYTES,
        AttachmentError,
        AttachmentStore,
        request_attachment,
    )
    from synapse_channel.federation_api._claims import (
        DEFAULT_FORWARD_TIMEOUT,
        ClaimForwardError,
        ClaimForwardRequest,
        ClaimForwardResult,
        ClaimWireError,
        encode_claim_forward_request,
        forward_claim,
    )
    from synapse_channel.federation_api._evidence import (
        CONTRACT_RESOURCE_ATTRIBUTE,
        CORRELATION_CONTRACT,
        CORRELATION_KINDS,
        CORRELATION_SCHEMA_URL,
        DEFAULT_EXPORT_TIMEOUT,
        DEFAULT_MAX_GRAPH_NODES,
        FEDERATION,
        GENERAL_RECORD,
        GRAPH_KINDS,
        LIFECYCLE,
        OPERATION_RECORD,
        RISK_INDICATOR,
        SERVICE_NAME,
        SPAN_ID_HEX_LENGTH,
        SPAN_STATUS_ERROR,
        SPAN_STATUS_UNSET,
        TASK_CAUSAL_PARENT_FIELD,
        CorrelationPoint,
        EventKind,
        FederatedCausalGraph,
        FederatedNode,
        HubEventRef,
        OtelProjection,
        OtelSpanRecord,
        SpanAttributeValue,
        SpanLinkRecord,
        TaskStatus,
        build_federated_graph,
        correlation_points,
        parse_task_causal_parent,
        projection_to_json,
        purpose_for_kind,
        push_projection,
        span_id_for_root,
        trace_id_for_task,
    )
    from synapse_channel.federation_api._health import (
        EntitlementAdvertError,
        build_local_fleet_health,
        validate_advert,
    )
    from synapse_channel.federation_api._identity import (
        IdentityKeyError,
        MTLSPeerTrustBundle,
        MTLSTrustedPeer,
        PeerRegistrationSigner,
        apply_sqlcipher_key,
        certificate_sha256_pin,
        certificate_sha256_pin_from_der,
        import_sqlcipher_module,
        load_key_file,
        load_peer_registration_signer,
        load_signing_key,
    )
    from synapse_channel.federation_api._peers import (
        PING_INTERVAL,
        STORE_VERSION,
        ClockSkew,
        EventFetcher,
        FederationBundle,
        FederationPeer,
        FederationRecord,
        HubEvent,
        MultiHubAuthorisation,
        MultiHubAuthoriser,
        ObservedBoardConflict,
        ObservedBoardProvenance,
        ObservedState,
        StoredEvent,
        authorise_multihub_peer,
        fold_observed_state,
        load_store,
        merge_event_logs,
        network_fetcher,
        peer_from_dict,
        save_store,
        tag_events,
    )
    from synapse_channel.federation_api._protocol import (
        WIRE_PROTOCOL_VERSION,
        MessageType,
        ProtocolNegotiation,
        SynapseAgent,
    )
    from synapse_channel.federation_api._receipts import (
        MerkleRoot,
        RowMacKey,
        RunningRoot,
        build_root,
        check_receipt_merkle_signature,
        load_or_create_row_mac_key,
        load_receipt_signing_key,
        load_receipt_verification_key,
        render_root_markdown,
        root_to_json,
        row_mac_key_path,
        run_root,
        sign_merkle_commitment,
        verify_root,
    )
    from synapse_channel.federation_api._spend import (
        DEFAULT_SPEND_TIMEOUT,
        Connector,
        OwnerRevocation,
        SpendEpochError,
        SpendTransportError,
        SpendTransportTimeoutError,
        request_spend,
        usable_grant,
        verify_owner_revocation,
    )

_CAPABILITY_OF: dict[str, str] = {
    # Following and merging peer hubs
    "ClockSkew": "_peers",
    "EventFetcher": "_peers",
    "FederationBundle": "_peers",
    "FederationPeer": "_peers",
    "FederationRecord": "_peers",
    "HubEvent": "_peers",
    "MultiHubAuthorisation": "_peers",
    "MultiHubAuthoriser": "_peers",
    "ObservedBoardConflict": "_peers",
    "ObservedBoardProvenance": "_peers",
    "ObservedState": "_peers",
    "PING_INTERVAL": "_peers",
    "STORE_VERSION": "_peers",
    "StoredEvent": "_peers",
    "authorise_multihub_peer": "_peers",
    "fold_observed_state": "_peers",
    "load_store": "_peers",
    "merge_event_logs": "_peers",
    "network_fetcher": "_peers",
    "peer_from_dict": "_peers",
    "save_store": "_peers",
    "tag_events": "_peers",
    # Claim forwarding
    "ClaimForwardError": "_claims",
    "ClaimForwardRequest": "_claims",
    "ClaimForwardResult": "_claims",
    "ClaimWireError": "_claims",
    "DEFAULT_FORWARD_TIMEOUT": "_claims",
    "encode_claim_forward_request": "_claims",
    "forward_claim": "_claims",
    # Shared spend
    "Connector": "_spend",
    "DEFAULT_SPEND_TIMEOUT": "_spend",
    "OwnerRevocation": "_spend",
    "SpendEpochError": "_spend",
    "SpendTransportError": "_spend",
    "SpendTransportTimeoutError": "_spend",
    "request_spend": "_spend",
    "usable_grant": "_spend",
    "verify_owner_revocation": "_spend",
    # Attachments
    "AttachmentError": "_attachments",
    "AttachmentStore": "_attachments",
    "MAX_CHUNK_BYTES": "_attachments",
    "request_attachment": "_attachments",
    # Evidence, causality and OpenTelemetry
    "CONTRACT_RESOURCE_ATTRIBUTE": "_evidence",
    "CORRELATION_CONTRACT": "_evidence",
    "CORRELATION_KINDS": "_evidence",
    "CORRELATION_SCHEMA_URL": "_evidence",
    "CorrelationPoint": "_evidence",
    "DEFAULT_EXPORT_TIMEOUT": "_evidence",
    "DEFAULT_MAX_GRAPH_NODES": "_evidence",
    "EventKind": "_evidence",
    "FEDERATION": "_evidence",
    "FederatedCausalGraph": "_evidence",
    "FederatedNode": "_evidence",
    "GENERAL_RECORD": "_evidence",
    "GRAPH_KINDS": "_evidence",
    "HubEventRef": "_evidence",
    "LIFECYCLE": "_evidence",
    "OPERATION_RECORD": "_evidence",
    "OtelProjection": "_evidence",
    "OtelSpanRecord": "_evidence",
    "RISK_INDICATOR": "_evidence",
    "SERVICE_NAME": "_evidence",
    "SPAN_ID_HEX_LENGTH": "_evidence",
    "SPAN_STATUS_ERROR": "_evidence",
    "SPAN_STATUS_UNSET": "_evidence",
    "SpanAttributeValue": "_evidence",
    "SpanLinkRecord": "_evidence",
    "TASK_CAUSAL_PARENT_FIELD": "_evidence",
    "TaskStatus": "_evidence",
    "build_federated_graph": "_evidence",
    "correlation_points": "_evidence",
    "parse_task_causal_parent": "_evidence",
    "projection_to_json": "_evidence",
    "purpose_for_kind": "_evidence",
    "push_projection": "_evidence",
    "span_id_for_root": "_evidence",
    "trace_id_for_task": "_evidence",
    # Receipts and integrity
    "MerkleRoot": "_receipts",
    "RowMacKey": "_receipts",
    "RunningRoot": "_receipts",
    "build_root": "_receipts",
    "check_receipt_merkle_signature": "_receipts",
    "load_or_create_row_mac_key": "_receipts",
    "load_receipt_signing_key": "_receipts",
    "load_receipt_verification_key": "_receipts",
    "render_root_markdown": "_receipts",
    "root_to_json": "_receipts",
    "row_mac_key_path": "_receipts",
    "run_root": "_receipts",
    "sign_merkle_commitment": "_receipts",
    "verify_root": "_receipts",
    # Identity, TLS and keys at rest
    "IdentityKeyError": "_identity",
    "MTLSPeerTrustBundle": "_identity",
    "MTLSTrustedPeer": "_identity",
    "PeerRegistrationSigner": "_identity",
    "apply_sqlcipher_key": "_identity",
    "certificate_sha256_pin": "_identity",
    "certificate_sha256_pin_from_der": "_identity",
    "import_sqlcipher_module": "_identity",
    "load_key_file": "_identity",
    "load_peer_registration_signer": "_identity",
    "load_signing_key": "_identity",
    # Health and entitlement
    "EntitlementAdvertError": "_health",
    "build_local_fleet_health": "_health",
    "validate_advert": "_health",
    # Protocol and client
    "MessageType": "_protocol",
    "ProtocolNegotiation": "_protocol",
    "SynapseAgent": "_protocol",
    "WIRE_PROTOCOL_VERSION": "_protocol",
}
"""Each promised name and the private submodule of its capability."""

__all__ = [
    "AttachmentError",
    "AttachmentStore",
    "CONTRACT_RESOURCE_ATTRIBUTE",
    "CORRELATION_CONTRACT",
    "CORRELATION_KINDS",
    "CORRELATION_SCHEMA_URL",
    "ClaimForwardError",
    "ClaimForwardRequest",
    "ClaimForwardResult",
    "ClaimWireError",
    "ClockSkew",
    "Connector",
    "CorrelationPoint",
    "DEFAULT_EXPORT_TIMEOUT",
    "DEFAULT_FORWARD_TIMEOUT",
    "DEFAULT_MAX_GRAPH_NODES",
    "DEFAULT_SPEND_TIMEOUT",
    "EntitlementAdvertError",
    "EventFetcher",
    "EventKind",
    "FEDERATION",
    "FederatedCausalGraph",
    "FederatedNode",
    "FederationBundle",
    "FederationPeer",
    "FederationRecord",
    "GENERAL_RECORD",
    "GRAPH_KINDS",
    "HubEvent",
    "HubEventRef",
    "IdentityKeyError",
    "LIFECYCLE",
    "MAX_CHUNK_BYTES",
    "MTLSPeerTrustBundle",
    "MTLSTrustedPeer",
    "MerkleRoot",
    "MessageType",
    "MultiHubAuthorisation",
    "MultiHubAuthoriser",
    "OPERATION_RECORD",
    "ObservedBoardConflict",
    "ObservedBoardProvenance",
    "ObservedState",
    "OtelProjection",
    "OtelSpanRecord",
    "OwnerRevocation",
    "PING_INTERVAL",
    "PeerRegistrationSigner",
    "ProtocolNegotiation",
    "RISK_INDICATOR",
    "RowMacKey",
    "RunningRoot",
    "SERVICE_NAME",
    "SPAN_ID_HEX_LENGTH",
    "SPAN_STATUS_ERROR",
    "SPAN_STATUS_UNSET",
    "STORE_VERSION",
    "SpanAttributeValue",
    "SpanLinkRecord",
    "SpendEpochError",
    "SpendTransportError",
    "SpendTransportTimeoutError",
    "StoredEvent",
    "SynapseAgent",
    "TASK_CAUSAL_PARENT_FIELD",
    "TaskStatus",
    "WIRE_PROTOCOL_VERSION",
    "apply_sqlcipher_key",
    "authorise_multihub_peer",
    "build_federated_graph",
    "build_local_fleet_health",
    "build_root",
    "certificate_sha256_pin",
    "certificate_sha256_pin_from_der",
    "check_receipt_merkle_signature",
    "correlation_points",
    "encode_claim_forward_request",
    "fold_observed_state",
    "forward_claim",
    "import_sqlcipher_module",
    "load_key_file",
    "load_or_create_row_mac_key",
    "load_peer_registration_signer",
    "load_receipt_signing_key",
    "load_receipt_verification_key",
    "load_signing_key",
    "load_store",
    "merge_event_logs",
    "network_fetcher",
    "parse_task_causal_parent",
    "peer_from_dict",
    "projection_to_json",
    "purpose_for_kind",
    "push_projection",
    "render_root_markdown",
    "request_attachment",
    "request_spend",
    "root_to_json",
    "row_mac_key_path",
    "run_root",
    "save_store",
    "sign_merkle_commitment",
    "span_id_for_root",
    "tag_events",
    "trace_id_for_task",
    "usable_grant",
    "validate_advert",
    "verify_owner_revocation",
    "verify_root",
]


def __getattr__(name: str) -> object:
    """Return ``name``, importing its capability on first use.

    Parameters
    ----------
    name : str
        A name in ``__all__``.

    Returns
    -------
    object
        The defining module's object, as the capability's module imported it.

    Raises
    ------
    AttributeError
        When ``name`` is not part of this surface.
    """
    capability = _CAPABILITY_OF.get(name)
    if capability is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{capability}"), name)


def __dir__() -> list[str]:
    """Return the promised names."""
    return list(__all__)
