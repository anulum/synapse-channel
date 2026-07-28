#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — read-only external transport effective-policy contract
# Policy prose stays one literal per dimension so JSON and grep evidence stay exact.
# ruff: noqa: E501
"""Enumerate and validate the security policy of every shipped external edge.

This is an evidence projection, not another configuration framework. It cannot
start a listener, grant authority, carry credentials, or mutate ``HubConfig``.
Each row names the production symbols that enforce the stated policy so CI can
fail when an edge disappears or its control surface is renamed without updating
the contract and its behavioural proof.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final

POLICY_DIMENSIONS: Final[tuple[str, ...]] = (
    "activation",
    "identity",
    "auth_acl",
    "encryption",
    "exposure",
    "request_size",
    "response_size",
    "timeout_concurrency",
    "sanitized_errors",
)

EXPECTED_EDGES: Final[tuple[str, ...]] = (
    "websocket",
    "hub-http",
    "a2a-http",
    "a2a-grpc",
    "dashboard",
    "metrics",
    "webhook",
    "mcp",
    "federation",
)


@dataclass(frozen=True)
class ExternalTransportPolicy:
    """One immutable, secret-free external-edge policy assertion."""

    edge: str
    activation: str
    identity: str
    auth_acl: str
    encryption: str
    exposure: str
    request_size: str
    response_size: str
    timeout_concurrency: str
    sanitized_errors: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible projection."""
        return asdict(self)


EXTERNAL_TRANSPORT_POLICIES: Final[tuple[ExternalTransportPolicy, ...]] = (
    ExternalTransportPolicy(
        edge="websocket",
        activation="enabled with the hub listener",
        identity="claimed agent name; optional signed identity binding and per-frame identity",
        auth_acl="first-frame shared token plus optional ACL, roles, and per-message authentication",
        encryption="ws on the accepted bind posture or wss through the supplied server TLS context",
        exposure="loopback default; unsafe off-loopback and plaintext-at-rest combinations fail closed",
        request_size="HubLimits.max_msg_bytes is passed to websockets max_size; bounded JSON depth",
        response_size="no second wire cap; responses derive from quota- and retention-bounded hub state",
        timeout_concurrency="auth timeout, total/unauthenticated/per-host client ceilings, and rate policy",
        sanitized_errors="typed error frames; internal exceptions and stack traces are not serialized",
        evidence=(
            "synapse_channel.core.hub:SynapseHub",
            "synapse_channel.core.hub_config:HubConfig",
            "synapse_channel.core.hub_exposure:guard_exposure",
            "synapse_channel.core.errors:error_code",
        ),
    ),
    ExternalTransportPolicy(
        edge="hub-http",
        activation="health is enabled on the hub listener; metrics is separately gated",
        identity="probe requests have no agent principal and cannot perform hub mutations",
        auth_acl="health is read-only; metrics uses its configured constant-time bearer check",
        encryption="inherits the hub listener ws/wss transport context",
        exposure="shares the guarded hub bind and accepts only /health or enabled /metrics",
        request_size="GET-only probe routes consume no request body",
        response_size="health is fixed-shape; metrics is derived from bounded in-memory counters",
        timeout_concurrency="inherits hub connection ceilings and HTTP handshake handling",
        sanitized_errors="fixed status, media type, and value-free unauthorized response",
        evidence=(
            "synapse_channel.core.hub_http:http_endpoint_response",
            "synapse_channel.core.hub_http:metrics_authorised",
            "synapse_channel.core.metrics:health_snapshot",
        ),
    ),
    ExternalTransportPolicy(
        edge="a2a-http",
        activation="enabled only by the explicit a2a-serve command",
        identity="one declared bridge identity connects to the hub; callers remain shared-bearer clients",
        auth_acl="constant-time bearer plus unconditional Host and present-Origin policy; no per-caller ACL",
        encryption="native TLS or mTLS when selected; plaintext off-loopback requires explicit acceptance",
        exposure="loopback default with a2a_bind_problems fail-closed startup checks",
        request_size="one MiB JSON body, bounded JSON depth, and exact Content-Length reads",
        response_size="no global byte cap; task history and stored state retain their own bounded contracts",
        timeout_concurrency="32 requests and 30-second read/operation budget by default",
        sanitized_errors="shared HTTP error boundary emits stable A2A problem responses",
        evidence=(
            "synapse_channel.a2a_http:serve_a2a_http",
            "synapse_channel.a2a_http:MAX_A2A_JSON_BODY_BYTES",
            "synapse_channel.a2a_http_protocol:origin_allowed",
            "synapse_channel.core.error_boundaries:http_error_boundary",
        ),
    ),
    ExternalTransportPolicy(
        edge="a2a-grpc",
        activation="default-off; enabled only with explicit --grpc-port",
        identity="inherits the same declared bridge identity; callers remain shared-bearer clients",
        auth_acl="same bridge bearer is required when armed; no unsupported per-caller ACL claim",
        encryption="CLI composes the same native TLS or mTLS certificate intent into gRPC credentials",
        exposure="same guarded host as A2A HTTP; no sibling plaintext bypass of a protected profile",
        request_size="one MiB receive ceiling and bounded JSON object parser",
        response_size="one MiB send ceiling enforced before serialization returns to gRPC",
        timeout_concurrency="32 RPCs and finite 30-second client/server deadline ceiling by default",
        sanitized_errors="stable value-free gRPC status details for auth, input, timeout, and internal failure",
        evidence=(
            "synapse_channel.a2a_grpc:A2AGrpcPolicy",
            "synapse_channel.a2a_grpc:build_a2a_grpc_server",
            "synapse_channel.cli_a2a_serve:_cmd_a2a_serve",
        ),
    ),
    ExternalTransportPolicy(
        edge="dashboard",
        activation="enabled only by the explicit dashboard command",
        identity="immutable dashboard principal and capability set plus one declared hub bridge name",
        auth_acl="generated or supplied bearer gates live reads; role capabilities gate every mutation",
        encryption="native server is HTTP; non-loopback deployment requires trusted network or TLS proxy",
        exposure="loopback default, explicit non-loopback acceptance, and Host authority enforcement",
        request_size="operator bodies are 64 KiB; setup requests are 4 KiB; routed reads are GET-only",
        response_size="fixed assets or bounded state/feed projections; no global streaming byte cap",
        timeout_concurrency="finite hub/observed-peer timeouts; threaded HTTP has no separate admission ceiling",
        sanitized_errors="fixed access/write errors; unavailable live snapshots expose only typed dashboard detail",
        evidence=(
            "synapse_channel.dashboard:start_dashboard_server",
            "synapse_channel.dashboard_access:DashboardAccessPolicy",
            "synapse_channel.dashboard_bind:validate_dashboard_bind",
            "synapse_channel.dashboard_operator_writes:MAX_OPERATOR_BODY_BYTES",
        ),
    ),
    ExternalTransportPolicy(
        edge="metrics",
        activation="default-off through HubMetricsConfig.enable_metrics",
        identity="read-only process telemetry has no agent principal",
        auth_acl="constant-time configured bearer; query-token compatibility is explicit and exposure-checked",
        encryption="inherits the hub listener ws/wss transport context",
        exposure="loopback default; non-loopback metrics requires a token and forbids query tokens",
        request_size="GET-only /metrics consumes no request body",
        response_size="Prometheus text is generated from the hub's bounded counter inventory",
        timeout_concurrency="inherits hub HTTP handshake and connection ceilings",
        sanitized_errors="fixed unauthorized response and numeric metric labels only",
        evidence=(
            "synapse_channel.core.hub_config:HubMetricsConfig",
            "synapse_channel.core.hub_exposure:exposure_problems",
            "synapse_channel.core.hub_http:metrics_authorised",
            "synapse_channel.core.metrics:render_prometheus",
        ),
    ),
    ExternalTransportPolicy(
        edge="webhook",
        activation="outbound only after an explicit A2A push-notification configuration",
        identity="task and push-config correlation; no claim of receiver principal identity",
        auth_acl="optional sensitive headers remain same-origin across redirects",
        encryption="HTTPS preserved; every downgrade fails closed",
        exposure="DNS resolves once, connects to pinned public addresses, and rejects local networks",
        request_size="payload is produced from already-admitted A2A task state",
        response_size="at most 64 KiB of receiver response is consumed",
        timeout_concurrency="caller supplies a finite timeout; redirect chain is capped at five",
        sanitized_errors="transport failures return typed delivery failure without response-body disclosure",
        evidence=(
            "synapse_channel.safe_webhook_transport:build_safe_opener",
            "synapse_channel.safe_webhook_transport:read_bounded",
            "synapse_channel.safe_webhook_transport:describe_webhook_redirect_policy",
        ),
    ),
    ExternalTransportPolicy(
        edge="mcp",
        activation="enabled only by the explicit MCP stdio command",
        identity="declared MCP bridge name and optional roles register through the normal hub client",
        auth_acl="hub token and hub ACL/role policy apply; MCP stdio peer trust is the parent-process boundary",
        encryption="stdio is local process I/O; the bridge URI independently selects ws or wss",
        exposure="no MCP network listener is opened by the shipped server",
        request_size="SDK-owned stdio framing; hub-bound frames retain the hub message/depth ceiling",
        response_size="SDK-owned stdio framing; returned resources derive from bounded hub projections",
        timeout_concurrency="five-second correlated hub request timeout and five-second startup readiness default",
        sanitized_errors="tool facades return stable messages; hub exceptions are not serialized as tracebacks",
        evidence=(
            "synapse_channel.mcp.stdio:serve_stdio",
            "synapse_channel.mcp.bridge:SynapseHubBridge",
            "synapse_channel.mcp.bridge:DEFAULT_REQUEST_TIMEOUT",
        ),
    ),
    ExternalTransportPolicy(
        edge="federation",
        activation="default-off until multi-hub or federation policy is supplied",
        identity="declared local hub id plus pinned mTLS peer and signed federation identity when required",
        auth_acl="hub token, serving grants, namespace scope, signatures, and ACL compose deny-by-default",
        encryption="wss default PKI or exact certificate pin; optional client certificate never weakens server pinning",
        exposure="serving uses the guarded hub listener; outbound peers are exact configured URIs",
        request_size="hub max message/depth ceiling; serving policy and trust files have explicit file caps",
        response_size="log request limit and hub retention bound snapshots; peer frames use the hub wire ceiling",
        timeout_concurrency="ten-second fetch/forward timeout defaults plus hub per-host/client ceilings",
        sanitized_errors="typed transport refusal preserves local authority and never advances state on failure",
        evidence=(
            "synapse_channel.core.multihub_serving:MultiHubServingPolicy",
            "synapse_channel.core.multihub_transport:network_fetcher",
            "synapse_channel.core.multihub_transport:pinned_connector",
            "synapse_channel.core.federation:FederationBundle",
        ),
    ),
)


def _resolve_reference(reference: str) -> object:
    """Resolve one ``module:attribute[.attribute]`` evidence reference."""
    module_name, separator, attribute_path = reference.partition(":")
    if not separator or not module_name or not attribute_path:
        raise ValueError(f"invalid evidence reference: {reference}")
    value: object = importlib.import_module(module_name)
    for attribute in attribute_path.split("."):
        value = getattr(value, attribute)
    return value


def contract_findings(
    policies: Sequence[ExternalTransportPolicy] = EXTERNAL_TRANSPORT_POLICIES,
) -> tuple[str, ...]:
    """Return stable findings for an incomplete or stale policy contract."""
    findings: list[str] = []
    edges = tuple(policy.edge for policy in policies)
    if edges != EXPECTED_EDGES:
        findings.append(f"edge inventory must be {EXPECTED_EDGES!r}, got {edges!r}")
    if len(set(edges)) != len(edges):
        findings.append("edge inventory contains duplicates")

    forbidden = {"", "unknown", "unspecified", "todo", "tbd"}
    for policy in policies:
        for dimension in POLICY_DIMENSIONS:
            value = getattr(policy, dimension).strip().lower()
            if value in forbidden:
                findings.append(f"{policy.edge}.{dimension} is not explicit")
        if not policy.evidence:
            findings.append(f"{policy.edge}.evidence is empty")
        for reference in policy.evidence:
            try:
                _resolve_reference(reference)
            except (AttributeError, ImportError, ValueError) as exc:
                findings.append(f"{policy.edge} evidence {reference!r} is unresolved: {exc}")
    return tuple(findings)


def build_parser() -> argparse.ArgumentParser:
    """Build the audit command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail on contract drift")
    mode.add_argument("--json", action="store_true", help="emit the complete contract as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate or emit the read-only transport policy contract."""
    args = build_parser().parse_args(argv)
    findings = contract_findings()
    if findings:
        for finding in findings:
            print(f"external transport policy: {finding}")
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": "synapse-external-transport-policy.v1",
                    "edges": [policy.to_dict() for policy in EXTERNAL_TRANSPORT_POLICIES],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            "external transport policy contract passed: "
            f"{len(EXTERNAL_TRANSPORT_POLICIES)} edges x {len(POLICY_DIMENSIONS)} dimensions"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess contract test.
    raise SystemExit(main())
