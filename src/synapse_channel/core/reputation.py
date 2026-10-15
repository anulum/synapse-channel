# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — opt-in advisory evidence ranking
"""Project recorded evidence into an explicit, provenance-linked triage order."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.event_row_mac import load_or_create_row_mac_key
from synapse_channel.core.record_keeping_source import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    iter_record_source,
)
from synapse_channel.core.trust_graph import (
    AGENT_NODE,
    CONFLICT_PAIR_EDGE,
    POSITIVE_RECEIPT_EDGE,
    TrustGraph,
    TrustGraphEdge,
    build_trust_graph,
    graph_involving,
)

SCHEMA = "agent-reputation.v1"
ADVISORY_NOTE = (
    "advisory reputation for human triage; does not authorise or deny execution; "
    "recorded declarations are gameable and are not independent verification of work"
)


@dataclass(frozen=True)
class AgentReputation:
    """One agent's unit-weight evidence tally and source sequences.

    Attributes
    ----------
    agent : str
        Recorded agent label.
    positive : int
        Number of positive receipt edges.
    negatives : tuple[tuple[str, int], ...]
        Other evidence edge counts sorted by kind; conflict pairs count for both endpoints.
    net : int
        Positive count minus the total negative count.
    evidence_seqs : tuple[int, ...]
        Unique source sequences sorted in ascending order.
    """

    agent: str
    positive: int
    negatives: tuple[tuple[str, int], ...]
    net: int
    evidence_seqs: tuple[int, ...]

    @property
    def negative_total(self) -> int:
        """Return the sum of negative evidence counts."""
        return sum(count for _, count in self.negatives)


@dataclass(frozen=True)
class ReputationReport:
    """A deterministic advisory order over the supplied evidence graph."""

    generated_from_seq: int
    as_of: float
    agents: tuple[AgentReputation, ...]
    advisory_note: str = ADVISORY_NOTE


def build_reputation(graph: TrustGraph) -> ReputationReport:
    """Count graph edges once and sort agents by net, positives, then label.

    Parameters
    ----------
    graph : TrustGraph
        Recorded evidence graph, optionally filtered by the caller.

    Returns
    -------
    ReputationReport
        An advisory ranking without any execution or routing decision.
    """
    involved: dict[str, list[TrustGraphEdge]] = defaultdict(list)
    for edge in graph.edges:
        involved[edge.source].append(edge)
        if edge.kind == CONFLICT_PAIR_EDGE and edge.target != edge.source:
            involved[edge.target].append(edge)
    agents: list[AgentReputation] = []
    for node in graph.nodes:
        if node.kind == AGENT_NODE:
            edges = involved[node.id]
            counts = Counter(edge.kind for edge in edges)
            positive = counts.pop(POSITIVE_RECEIPT_EDGE, 0)
            agents.append(
                AgentReputation(
                    node.label,
                    positive,
                    tuple(sorted(counts.items())),
                    positive - sum(counts.values()),
                    tuple(sorted({edge.seq for edge in edges})),
                )
            )
    agents.sort(key=lambda item: (-item.net, -item.positive, item.agent))
    return ReputationReport(graph.generated_from_seq, graph.as_of, tuple(agents))


def run_reputation(
    db_path: str | Path,
    *,
    as_of: float | None = None,
    agent: str | None = None,
    since: float | None = None,
    key_file: str | Path | None = None,
    row_mac_key_file: str | Path | None = None,
    through_seq: int | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> ReputationReport:
    """Rank one validated, complete, read-only journal snapshot.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Existing SQLite or SQLCipher event journal; never created or migrated.
    as_of, since : float or None, optional
        Finite reference clock and inclusive evidence timestamp filter.
    agent : str or None, optional
        Keep this agent's evidence, including both endpoints of shared conflicts.
    key_file, row_mac_key_file : str or pathlib.Path or None, optional
        Existing SQLCipher and trusted enrolled row keys. No key is provisioned.
    through_seq : int or None, optional
        Inclusive snapshot ceiling; zero selects no rows.
    max_events, max_payload_bytes : int, optional
        Complete-read budgets. Exhaustion refuses rather than truncating a ranking.

    Returns
    -------
    ReputationReport
        Ranked recorded declarations; row authentication does not prove their truth.

    Raises
    ------
    ValueError, OSError, RuntimeError
        Invalid clocks, storage, keys, persisted records or exhausted budgets.
    """
    for clock in (as_of, since):
        if clock is not None and (
            isinstance(clock, bool)
            or not isinstance(clock, int | float)
            or not math.isfinite(clock)
        ):
            raise ValueError("ranking clocks must be finite numbers")
    row_key = (
        None
        if row_mac_key_file is None
        else load_or_create_row_mac_key(row_mac_key_file, current_max_seq=0, log_has_macs=True)
    )
    events = [
        row.event
        for row in iter_record_source(
            db_path,
            key_file=key_file,
            row_mac=row_key,
            through_seq=through_seq,
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
        )
    ]
    try:
        graph = graph_involving(build_trust_graph(events, as_of=as_of), agent=agent, since=since)
    except (TypeError, OverflowError) as exc:
        raise ValueError("invalid recorded evidence for advisory ranking") from exc
    return build_reputation(graph)


def reputation_to_json(report: ReputationReport) -> dict[str, object]:
    """Return ranking counts and provenance with an explicit authority boundary.

    Parameters
    ----------
    report : ReputationReport
        Advisory projection to encode.

    Returns
    -------
    dict[str, object]
        Stable JSON-compatible shape, with authorises_execution always false.
    """
    return {
        "schema": SCHEMA,
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "advisory_note": report.advisory_note,
        "authorises_execution": False,
        "agents": [
            {
                "agent": agent.agent,
                "net": agent.net,
                "positive": agent.positive,
                "negatives": dict(agent.negatives),
                "evidence_seqs": list(agent.evidence_seqs),
            }
            for agent in report.agents
        ],
    }


def render_reputation(report: ReputationReport) -> str:
    """Render the advisory order, counts and source sequences as terminal text.

    Parameters
    ----------
    report : ReputationReport
        Advisory projection to display.

    Returns
    -------
    str
        Human-readable ranking, including an explicit empty-result message.
    """
    lines = [
        f"Agent reputation (advisory): {report.advisory_note}",
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        "",
    ]
    if not report.agents:
        lines.append("No agent evidence found.")
    for rank, agent in enumerate(report.agents, start=1):
        negatives = ", ".join(f"{kind}={count}" for kind, count in agent.negatives) or "none"
        seqs = ", ".join(str(seq) for seq in agent.evidence_seqs)
        label = json.dumps(agent.agent, ensure_ascii=True)[1:-1]
        lines.append(
            f"#{rank} {label}: net={agent.net} "
            f"(+{agent.positive} receipts, -{agent.negative_total} [{negatives}]) "
            f"evidence seqs=[{seqs}]"
        )
    return "\n".join(lines)
