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
validation gates visible until independent clients and webhook receivers exercise
them.
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

NORMATIVE_SOURCE_URL = "https://github.com/a2aproject/A2A/blob/main/spec/a2a.proto"
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
        evidence="Bridge task creation, metadata correlation, state persistence, and HTTP tests.",
        limitation=(
            "The bridge always returns a task wrapper immediately; it does not implement the "
            "blocking-by-default direct Message response profile."
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
        limitation="Ordering is deterministic by task id rather than status timestamp descending.",
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
        limitation="Subscriptions are memory-only and reject terminal recovered tasks.",
        spec_reference="A2A 1.0.0 §3.1.6",
    ),
    A2AConformanceRow(
        area="operation",
        item="Push Notification Configs",
        status="partial",
        synapse_surface=(
            "POST|GET|DELETE /tasks/{id}/pushNotificationConfigs[/config_id]; "
            "JSON-RPC tasks/pushNotificationConfig/*"
        ),
        evidence=(
            "Config persistence, SSRF guard, delivery envelope, failure paths, and real "
            "local HTTPS/proxy receiver plus DNS-rebinding guard tests."
        ),
        limitation=(
            "Remote public receivers, retry policy, and operator-signoff traces remain external."
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
            "Real localhost HTTP tests exercise discovery, message, task, subscription, "
            "and push routes."
        ),
        limitation="External reverse-proxy and TLS deployment validation remains open.",
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
        status="unsupported",
        synapse_surface="none",
        evidence="No gRPC server, dependency, or CLI surface exists in this package.",
        limitation="A gRPC adapter would be a separate optional surface.",
        spec_reference="A2A 1.0.0 §10",
    ),
    A2AConformanceRow(
        area="validation",
        item="Independent interoperability",
        status="external",
        synapse_surface="docs/a2a-validation-receipts.md",
        evidence="Receipt template exists; no independent trace is recorded in this checkout.",
        limitation="Requires third-party A2A clients or servers and captured traces.",
        spec_reference="A2A 1.0.0 goals and operation model",
    ),
    A2AConformanceRow(
        area="validation",
        item="Real webhook receiver",
        status="partial",
        synapse_surface="docs/a2a-validation-receipts.md",
        evidence=(
            "Focused tests POST to real local HTTPS receivers with a test CA and through a "
            "real 307 proxy redirect; delivery-time DNS rebinding is blocked before send."
        ),
        limitation=(
            "Remote public receivers, production TLS termination, and operator-visible receipts "
            "remain external."
        ),
        spec_reference="A2A 1.0.0 push notification operations",
    ),
    A2AConformanceRow(
        area="validation",
        item="Deployment threat model",
        status="external",
        synapse_surface="docs/deployment.md; docs/a2a-validation-receipts.md",
        evidence="Deployment boundaries are documented; no signed deployment review is recorded.",
        limitation=(
            "Requires a concrete exposed deployment with auth, TLS, logging, retention, "
            "and egress review."
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
