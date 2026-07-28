# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent conformance matrix
"""Agent2Agent bridge conformance matrix.

The matrix is an operator-facing inventory, not a certification. It maps the
current local bridge surface to the A2A 1.0.0 operation model and keeps external
validation gates visible after each independently exercised boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConformanceStatus = Literal["supported", "partial", "unsupported", "external"]
"""Status labels used by the A2A conformance matrix."""

SPEC_VERSION = "1.0.0"
"""A2A specification version used by this matrix."""

SPECIFICATION_URL = "https://a2a-protocol.org/v1.0.0/specification"
"""Human-readable A2A specification URL used as the comparison source."""

NORMATIVE_SOURCE_URL = (
    "https://github.com/a2aproject/A2A/blob/"
    "173695755607e884aa9acf8ce4feed90e32727a1/specification/a2a.proto"
)
"""A2A normative proto source referenced by the specification."""

STATUS_MEANINGS: dict[ConformanceStatus, str] = {
    "supported": "Covered by the local bridge and focused repository tests.",
    "partial": "Implemented with a documented limitation or narrower local semantics.",
    "unsupported": "Not implemented by the local bridge.",
    "external": "Requires independent infrastructure, client, or operator validation.",
}
"""Operator-facing meaning of each status label."""


@dataclass(frozen=True)
class A2AConformanceRow:
    """One row in the A2A conformance matrix.

    Parameters
    ----------
    area : str
        Protocol area, such as ``"operation"`` or ``"binding"``.
    item : str
        A2A operation, binding, or validation topic.
    status : ConformanceStatus
        Current support status.
    synapse_surface : str
        SYNAPSE CLI, HTTP route, or module that implements or tracks the item.
    evidence : str
        Local evidence proving the current status.
    limitation : str
        Boundaries that remain true even when the row is supported locally.
    spec_reference : str
        A2A section or artifact used for comparison.
    """

    area: str
    item: str
    status: ConformanceStatus
    synapse_surface: str
    evidence: str
    limitation: str
    spec_reference: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serialisable row representation."""
        return {
            "area": self.area,
            "item": self.item,
            "status": self.status,
            "synapse_surface": self.synapse_surface,
            "evidence": self.evidence,
            "limitation": self.limitation,
            "spec_reference": self.spec_reference,
        }


CONFORMANCE_ROWS: tuple[A2AConformanceRow, ...] = (
    A2AConformanceRow(
        area="operation",
        item="Get Agent Card",
        status="supported",
        synapse_surface="synapse a2a-card; GET /.well-known/agent-card.json",
        evidence="Manifest-to-Agent-Card projection and real HTTP route tests.",
        limitation="The card is a bridge projection over SYNAPSE capabilities.",
        spec_reference="A2A 1.0.0 §3.1.11 and AgentCard model",
    ),
    A2AConformanceRow(
        area="operation",
        item="Send Message",
        status="partial",
        synapse_surface="POST /message:send; JSON-RPC message/send",
        evidence=(
            "Bridge task creation, metadata correlation, state persistence, HTTP tests, "
            "and residual TCK structured Message/Artifact scenario handlers."
        ),
        limitation=(
            "Default sends still return an asynchronous working Task (SYNAPSE-forward). "
            "Direct Message and completed structured-Artifact responses are supported for "
            "named scenarios (official TCK messageId prefixes and explicit a2aScenario "
            "metadata/configuration), not as the default for every ordinary chat send."
        ),
        spec_reference="A2A 1.0.0 §3.1.1 and §3.2.2",
    ),
    A2AConformanceRow(
        area="operation",
        item="Send Streaming Message",
        status="partial",
        synapse_surface="POST /message:stream; JSON-RPC message/stream",
        evidence="Server-Sent Events response tests over the stdlib HTTP bridge.",
        limitation=(
            "Streaming is bounded to the current bridge process and local lifecycle events; "
            "durable replay across restart is not claimed."
        ),
        spec_reference="A2A 1.0.0 §3.1.2",
    ),
    A2AConformanceRow(
        area="operation",
        item="Get Task",
        status="supported",
        synapse_surface="GET /tasks/{id}; JSON-RPC tasks/get",
        evidence="Task store and HTTP route tests cover lookup, unknown task, and history length.",
        limitation="Tasks are bridge-local, not native hub tasks.",
        spec_reference="A2A 1.0.0 §3.1.3",
    ),
    A2AConformanceRow(
        area="operation",
        item="List Tasks",
        status="partial",
        synapse_surface="GET /tasks; JSON-RPC tasks/list",
        evidence="Task listing, state filter, and cursor-shaped pagination are covered locally.",
        limitation=(
            "Ordering is status-update timestamp descending with task-id ascending "
            "tie-break; timestamps come from bridge-local metadata."
        ),
        spec_reference="A2A 1.0.0 §3.1.4",
    ),
    A2AConformanceRow(
        area="operation",
        item="Cancel Task",
        status="partial",
        synapse_surface="POST /tasks/{id}:cancel; JSON-RPC tasks/cancel",
        evidence="Terminal-state immutability and cancellation paths are covered by bridge tests.",
        limitation=(
            "Cancellation updates the bridge task and does not interrupt a remote agent process."
        ),
        spec_reference="A2A 1.0.0 §3.1.5",
    ),
    A2AConformanceRow(
        area="operation",
        item="Subscribe to Task",
        status="partial",
        synapse_surface="POST /tasks/{id}:subscribe",
        evidence="SSE subscription tests cover snapshot delivery and bounded queued updates.",
        limitation=(
            "Live queues are process-local; lifecycle history is durable when a state "
            "file is configured so restarted bridges can replay prior snapshots. "
            "Terminal recovered tasks still reject new subscriptions."
        ),
        spec_reference="A2A 1.0.0 §3.1.6",
    ),
    A2AConformanceRow(
        area="operation",
        item="Push Notification Configs",
        status="partial",
        synapse_surface=(
            "POST|GET|DELETE /tasks/{id}/pushNotificationConfigs[/config_id]; "
            "GET /tasks/{id}/pushNotificationDeliveries; JSON-RPC "
            "tasks/pushNotificationConfig/* and tasks/pushNotificationDelivery/list"
        ),
        evidence=(
            "Config persistence, SSRF guard, delivery envelope, bounded retries, durable "
            "credential-free attempt evidence, task-independent terminal dead letters, and "
            "real local HTTPS/proxy receiver plus DNS-rebinding guard tests."
        ),
        limitation=(
            "Remote public receiver acknowledgement, cross-replica outbox semantics, and "
            "operator-signoff traces remain external."
        ),
        spec_reference="A2A 1.0.0 §3.1.7-§3.1.10",
    ),
    A2AConformanceRow(
        area="operation",
        item="Get Extended Agent Card",
        status="partial",
        synapse_surface="GET /extendedAgentCard; JSON-RPC agent/getAuthenticatedExtendedCard",
        evidence="Protected route and JSON-RPC dispatch are wired through the same bridge card.",
        limitation="The authenticated card currently matches the public projection.",
        spec_reference="A2A 1.0.0 §3.1.11",
    ),
    A2AConformanceRow(
        area="binding",
        item="HTTP+JSON/REST",
        status="partial",
        synapse_surface="synapse a2a-serve",
        evidence=(
            "Official a2a-sdk 1.1.0 selected HTTP+JSON RestTransport and completed "
            "discovery/send/get/list/cancel; official TCK 5996b79 exercises the binding; "
            "native HTTPS bind and independent HTTPS interop-trace are covered locally. "
            "Every route enforces the advertised Host authority before auth/routing, "
            "with present browser Origins independently default-denied."
        ),
        limitation=(
            "In-repo residual structured Message/Artifact handlers close the five TCK "
            "content scenarios; re-running the external TCK for a fresh receipt remains "
            "optional evidence. Reverse-proxy production sign-off stays external."
        ),
        spec_reference="A2A 1.0.0 §11",
    ),
    A2AConformanceRow(
        area="binding",
        item="JSON-RPC 2.0",
        status="supported",
        synapse_surface="POST /rpc; A2ABridge.handle_json_rpc",
        evidence="JSON-RPC dispatch tests cover supported methods and error shapes.",
        limitation="Only methods backed by the local bridge are exposed.",
        spec_reference="A2A 1.0.0 §9",
    ),
    A2AConformanceRow(
        area="binding",
        item="gRPC",
        status="partial",
        synapse_surface=(
            "synapse a2a-serve --grpc-port; synapse_channel.a2a_grpc "
            "(optional grpcio / [a2a-grpc] extra)"
        ),
        evidence=(
            "Optional gRPC service synapse.a2a.v1.A2ABridge exposes SendMessage and "
            "GetTask over JSON-serialised payloads; Agent Card can advertise a GRPC "
            "interface URL. Focused real client/server tests cover shared-bearer "
            "authentication, TLS/mTLS, message bounds, finite deadlines, admission "
            "recovery, stable errors, and startup cleanup."
        ),
        limitation=(
            "Custom SYNAPSE JSON-over-gRPC binding, not a generated official A2A "
            "proto stub set; install grpcio separately; full multi-method A2A gRPC "
            "surface remains incomplete. The listener is default-off. The integrated "
            "CLI composes its selected shared bearer, native TLS/mTLS files, request "
            "concurrency ceiling, one-MiB message bounds, bounded JSON parser, finite "
            "call-deadline ceiling, and value-free error policy into gRPC. The shared "
            "bearer is not per-client identity or method-level ACL."
        ),
        spec_reference="A2A 1.0.0 §10",
    ),
    A2AConformanceRow(
        area="validation",
        item="Independent interoperability",
        status="partial",
        synapse_surface=(
            "synapse a2a-interop-trace; synapse a2a-client; "
            "synapse_channel.a2a_client; docs/a2a-validation-receipts.md"
        ),
        evidence=(
            "Official a2a-sdk 1.1.0 completed Agent Card discovery plus "
            "send/get/list/cancel over RestTransport; official A2A TCK 5996b79 "
            "historically finished 55/5/175 on HTTP+JSON MUST; in-repo residual "
            "handlers, dual HTTPS interop-trace, and outbound a2a-client dual-peer "
            "runs cover discovery/send/get; optional gRPC SendMessage/GetTask tests."
        ),
        limitation=(
            "This is partial validation, not certification: public webhook operator "
            "sign-off, reverse-proxy production attestation, and full external TCK "
            "certification remain open. Outbound client (`synapse a2a-client`) and "
            "local dual-process passes are covered in-repo."
        ),
        spec_reference="A2A 1.0.0 goals and operation model",
    ),
    A2AConformanceRow(
        area="validation",
        item="Real webhook receiver",
        status="partial",
        synapse_surface="docs/a2a-validation-receipts.md",
        evidence=(
            "Focused tests POST to real local HTTPS receivers with a test CA and through a "
            "real 307 proxy redirect; delivery-time DNS rebinding is blocked before send. "
            "Authenticated 301/302/303, cross-origin 307/308, and every HTTPS downgrade "
            "are refused; exact same-origin 307/308 preserve POST under a five-hop cap."
        ),
        limitation=(
            "Remote public receivers, initial authenticated-URL HTTPS enforcement, "
            "production TLS termination, and operator-visible receipts remain external."
        ),
        spec_reference="A2A 1.0.0 push notification operations",
    ),
    A2AConformanceRow(
        area="validation",
        item="Deployment threat model",
        status="partial",
        synapse_surface="docs/a2a-deployment-threat-model.md; docs/deployment.md",
        evidence=(
            "A2A-specific exposed-bridge threat model documents auth, TLS/proxy, state-file, "
            "webhook egress, retention, logging, and receipt requirements."
        ),
        limitation=(
            "Concrete production deployment, public receiver validation, and operator sign-off "
            "remain external."
        ),
        spec_reference="A2A 1.0.0 security guidance",
    ),
)
"""Current A2A bridge conformance rows."""


def conformance_rows(*, status: ConformanceStatus | None = None) -> tuple[A2AConformanceRow, ...]:
    """Return conformance rows, optionally filtered by status.

    Parameters
    ----------
    status : ConformanceStatus or None, optional
        Status filter. ``None`` returns every row.

    Returns
    -------
    tuple[A2AConformanceRow, ...]
        Matching rows in stable display order.
    """
    if status is None:
        return CONFORMANCE_ROWS
    return tuple(row for row in CONFORMANCE_ROWS if row.status == status)


def conformance_report(*, status: ConformanceStatus | None = None) -> dict[str, object]:
    """Return the A2A conformance report as JSON-serialisable data.

    Parameters
    ----------
    status : ConformanceStatus or None, optional
        Status filter. ``None`` returns every row.

    Returns
    -------
    dict[str, object]
        Report metadata, status meanings, and matrix rows.
    """
    rows = conformance_rows(status=status)
    return {
        "spec_version": SPEC_VERSION,
        "specification_url": SPECIFICATION_URL,
        "normative_source_url": NORMATIVE_SOURCE_URL,
        "status_meanings": dict(STATUS_MEANINGS),
        "rows": [row.to_dict() for row in rows],
    }


def render_conformance_markdown(*, status: ConformanceStatus | None = None) -> str:
    """Render the A2A conformance report as a Markdown table.

    Parameters
    ----------
    status : ConformanceStatus or None, optional
        Status filter. ``None`` renders every row.

    Returns
    -------
    str
        Markdown suitable for terminals and documentation snippets.
    """
    lines = [
        f"A2A conformance matrix (spec {SPEC_VERSION})",
        "",
        f"Specification: {SPECIFICATION_URL}",
        f"Normative source: {NORMATIVE_SOURCE_URL}",
        "",
        "| Area | Item | Status | SYNAPSE surface | Evidence | Limitation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in conformance_rows(status=status):
        lines.append(
            "| "
            + " | ".join(
                _escape_cell(value)
                for value in (
                    row.area,
                    row.item,
                    row.status,
                    row.synapse_surface,
                    row.evidence,
                    row.limitation,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    """Escape Markdown table separators in one cell."""
    return value.replace("|", "\\|").replace("\n", " ")
