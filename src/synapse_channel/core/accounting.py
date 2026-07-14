# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in model cost and token accounting from coordination events
"""Aggregate opt-in model usage and budget evidence from the durable hub event log.

Synapse never calls a model provider and never collects telemetry: a worker
waiter spends no tokens while it waits. Token and cost figures therefore only
exist if an agent or operator *chooses* to record them. The recording rides on
the existing progress-ledger channel — a ``LEDGER_PROGRESS`` note with
``kind="usage"`` and a canonical ``key=value`` text body — so no new wire
message, hub handler, or stored-event kind is introduced.

This module reads those notes back from a hub SQLite event store and aggregates
them into per-agent and per-model summaries, estimates cost either from the
recorded amount or from an optional local pricing table, and reports budget
evidence against an optional local budget table. Budgets are evidence, not an
enforcement gate: the report states spend against a ceiling, it does not block
work. The canonical note format is also exposed (:func:`format_usage_note` /
:func:`parse_usage_note`) so non-Python clients can emit the identical body.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.terminal_text import terminal_text

USAGE_NOTE_KIND = "usage"
"""Progress-note ``kind`` marking a structured model-usage record."""

USAGE_NOTE_PREFIX = "usage"
"""Leading token of a canonical usage-note text body."""

_TOKENS_PER_PRICE_UNIT = 1000.0
"""Token count that a per-unit price refers to (price is quoted per 1k tokens)."""


@dataclass(frozen=True)
class ModelPrice:
    """Local price for one model, quoted per 1000 tokens.

    Parameters
    ----------
    input_per_1k : float
        Cost of 1000 prompt/input tokens, in the report's currency unit.
    output_per_1k : float
        Cost of 1000 completion/output tokens, in the report's currency unit.
    """

    input_per_1k: float
    output_per_1k: float

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        """Return the estimated cost for a token split under this price.

        Parameters
        ----------
        input_tokens : int
            Number of input tokens.
        output_tokens : int
            Number of output tokens.

        Returns
        -------
        float
            Estimated cost in the report's currency unit.
        """
        return (
            input_tokens * self.input_per_1k + output_tokens * self.output_per_1k
        ) / _TOKENS_PER_PRICE_UNIT


@dataclass(frozen=True)
class UsageRecord:
    """One opt-in model-usage observation parsed from a progress note.

    Attributes
    ----------
    agent : str
        Recording agent identity (the progress-note author).
    model : str
        Model identifier the usage is attributed to.
    task_id : str
        Task the usage was recorded against; may be empty.
    calls : int
        Number of model calls represented by the record.
    input_tokens : int
        Input/prompt tokens consumed.
    output_tokens : int
        Output/completion tokens produced.
    recorded_cost : float or None
        Cost supplied by the recorder, or ``None`` when only tokens were given.
    seq : int
        Durable event-log sequence the record was observed at.
    ts : float
        Event timestamp.
    """

    agent: str
    model: str
    task_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    recorded_cost: float | None
    seq: int
    ts: float

    @property
    def total_tokens(self) -> int:
        """Return the sum of input and output tokens."""
        return self.input_tokens + self.output_tokens

    def estimated_cost(self, pricing: Mapping[str, ModelPrice] | None) -> float:
        """Return the cost estimate for this record.

        The optional pricing table wins when it prices the record's model;
        otherwise the recorder-supplied cost is used, defaulting to zero.

        Parameters
        ----------
        pricing : collections.abc.Mapping[str, ModelPrice] or None
            Local per-model price table, or ``None`` to use recorded cost only.

        Returns
        -------
        float
            Estimated cost in the report's currency unit.
        """
        if pricing is not None and self.model in pricing:
            return pricing[self.model].estimate(self.input_tokens, self.output_tokens)
        return self.recorded_cost if self.recorded_cost is not None else 0.0


@dataclass(frozen=True)
class UsageSummary:
    """Aggregate usage totals for one grouping key (agent or model).

    Attributes
    ----------
    key : str
        The agent identity or model id this summary aggregates.
    calls : int
        Total model calls.
    input_tokens : int
        Total input tokens.
    output_tokens : int
        Total output tokens.
    estimated_cost : float
        Total estimated cost in the report's currency unit.
    counterparts : tuple[str, ...]
        For an agent summary, the models used; for a model summary, the agents.
    """

    key: str
    calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    counterparts: tuple[str, ...]

    @property
    def total_tokens(self) -> int:
        """Return the sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class BudgetStatus:
    """Budget evidence for one agent.

    The status is descriptive evidence, not an enforcement decision: it reports
    spend against a declared ceiling without blocking any work.

    Attributes
    ----------
    agent : str
        Agent identity the budget applies to.
    budget : float
        Declared spend ceiling in the report's currency unit.
    spent : float
        Estimated spend attributed to the agent.
    over_budget : bool
        Whether estimated spend meets or exceeds the ceiling.
    """

    agent: str
    budget: float
    spent: float

    @property
    def remaining(self) -> float:
        """Return the remaining budget, never below zero."""
        return max(0.0, self.budget - self.spent)

    @property
    def over_budget(self) -> bool:
        """Return whether estimated spend reached or passed the ceiling."""
        return self.spent >= self.budget


@dataclass(frozen=True)
class AccountingTotals:
    """Fleet-wide usage totals across every record in the report."""

    calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float

    @property
    def total_tokens(self) -> int:
        """Return the sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AccountingReport:
    """Opt-in cost/token accounting built from a durable event store."""

    generated_from_seq: int
    as_of: float
    records: tuple[UsageRecord, ...]
    agents: tuple[UsageSummary, ...]
    models: tuple[UsageSummary, ...]
    budgets: tuple[BudgetStatus, ...]
    totals: AccountingTotals

    @property
    def summary_by_agent(self) -> dict[str, UsageSummary]:
        """Return agent summaries keyed by agent identity."""
        return {summary.key: summary for summary in self.agents}


def format_usage_note(
    *,
    model: str,
    calls: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float | None = None,
) -> str:
    """Return the canonical text body for a model-usage progress note.

    Emit the result as a ``LEDGER_PROGRESS`` note with ``kind="usage"`` (see
    :data:`USAGE_NOTE_KIND`). The format is a stable, client-agnostic
    ``key=value`` line so Python, Go, and JavaScript clients can record
    identical usage.

    Parameters
    ----------
    model : str
        Model identifier; must be non-empty and contain no spaces.
    calls : int, optional
        Number of model calls represented, by default 1.
    input_tokens, output_tokens : int, optional
        Token split for the record, by default 0.
    cost : float or None, optional
        Recorder-supplied cost; omitted from the body when ``None``.

    Returns
    -------
    str
        Canonical usage-note text body.

    Raises
    ------
    ValueError
        If ``model`` is empty or contains whitespace, or any count is negative.
    """
    cleaned = model.strip()
    if not cleaned or any(character.isspace() for character in cleaned):
        msg = "usage-note model must be non-empty and contain no whitespace"
        raise ValueError(msg)
    if min(calls, input_tokens, output_tokens) < 0:
        msg = "usage-note counts must not be negative"
        raise ValueError(msg)
    fields = [
        USAGE_NOTE_PREFIX,
        f"model={cleaned}",
        f"calls={int(calls)}",
        f"input_tokens={int(input_tokens)}",
        f"output_tokens={int(output_tokens)}",
    ]
    if cost is not None:
        if cost < 0:
            msg = "usage-note cost must not be negative"
            raise ValueError(msg)
        fields.append(f"cost_usd={float(cost):.6f}")
    return " ".join(fields)


def parse_usage_note(text: str) -> dict[str, Any] | None:
    """Parse a canonical usage-note body into its fields.

    Parameters
    ----------
    text : str
        Progress-note text body.

    Returns
    -------
    dict[str, Any] or None
        Parsed fields (``model``, ``calls``, ``input_tokens``,
        ``output_tokens``, and optional ``cost``), or ``None`` when the body is
        not a usage note or omits a usable model.
    """
    tokens = text.split()
    if not tokens or tokens[0] != USAGE_NOTE_PREFIX:
        return None
    pairs: dict[str, str] = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator:
            pairs[key] = value
    model = pairs.get("model", "").strip()
    if not model:
        return None
    parsed: dict[str, Any] = {
        "model": model,
        "calls": _coerce_int(pairs.get("calls"), default=1),
        "input_tokens": _coerce_int(pairs.get("input_tokens"), default=0),
        "output_tokens": _coerce_int(pairs.get("output_tokens"), default=0),
    }
    cost = _coerce_float(pairs.get("cost_usd"))
    if cost is not None:
        parsed["cost"] = cost
    return parsed


def run_accounting_report(
    db_path: str | Path,
    *,
    pricing: Mapping[str, ModelPrice] | None = None,
    budgets: Mapping[str, float] | None = None,
    key_file: str | Path | None = None,
) -> AccountingReport:
    """Build a cost/token accounting report from a hub SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    pricing : collections.abc.Mapping[str, ModelPrice] or None, optional
        Local per-model price table for cost estimation.
    budgets : collections.abc.Mapping[str, float] or None, optional
        Local per-agent spend ceilings for budget evidence.

    Returns
    -------
    AccountingReport
        Aggregated opt-in usage and budget evidence.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return build_accounting_report(events, pricing=pricing, budgets=budgets)


def build_accounting_report(
    events: Sequence[StoredEvent],
    *,
    pricing: Mapping[str, ModelPrice] | None = None,
    budgets: Mapping[str, float] | None = None,
) -> AccountingReport:
    """Build a cost/token accounting report from loaded events.

    Parameters
    ----------
    events : collections.abc.Sequence[StoredEvent]
        Durable events read from a hub event store.
    pricing : collections.abc.Mapping[str, ModelPrice] or None, optional
        Local per-model price table for cost estimation.
    budgets : collections.abc.Mapping[str, float] or None, optional
        Local per-agent spend ceilings for budget evidence.

    Returns
    -------
    AccountingReport
        Aggregated opt-in usage and budget evidence.
    """
    records = _usage_records(events)
    agents = _summaries(records, pricing, by_agent=True)
    models = _summaries(records, pricing, by_agent=False)
    spend_by_agent = {summary.key: summary.estimated_cost for summary in agents}
    return AccountingReport(
        generated_from_seq=max((event.seq for event in events), default=0),
        as_of=max((event.ts for event in events), default=0.0),
        records=records,
        agents=agents,
        models=models,
        budgets=_budget_statuses(budgets, spend_by_agent),
        totals=_totals(records, pricing),
    )


def accounting_to_json(report: AccountingReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of an accounting report."""
    return {
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "totals": _totals_to_json(report.totals),
        "agents": [_summary_to_json(summary) for summary in report.agents],
        "models": [_summary_to_json(summary) for summary in report.models],
        "budgets": [_budget_to_json(status) for status in report.budgets],
        "records": [_record_to_json(record, report) for record in report.records],
        "note": "opt-in usage evidence, not telemetry or an enforcement gate",
    }


def render_human(report: AccountingReport) -> str:
    """Render a cost/token accounting report as compact terminal text."""
    header = "Model cost/token accounting: opt-in evidence, not telemetry"
    if not report.records:
        return f"{header}\n\nNo recorded model usage found."
    totals = report.totals
    lines = [
        header,
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        (
            f"totals: calls={totals.calls} tokens={totals.total_tokens} "
            f"(in={totals.input_tokens} out={totals.output_tokens}) "
            f"est_cost={totals.estimated_cost:.4f}"
        ),
        "",
        "By agent",
    ]
    lines.extend(_render_summary(summary) for summary in report.agents)
    lines.append("")
    lines.append("By model")
    lines.extend(_render_summary(summary) for summary in report.models)
    if report.budgets:
        lines.append("")
        lines.append("Budgets (evidence, not enforcement)")
        lines.extend(_render_budget(status) for status in report.budgets)
    return "\n".join(lines)


def _usage_records(events: Sequence[StoredEvent]) -> tuple[UsageRecord, ...]:
    """Return usage records parsed from ``kind="usage"`` progress notes."""
    records: list[UsageRecord] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS:
            continue
        if str(event.payload.get("kind", "")) != USAGE_NOTE_KIND:
            continue
        parsed = parse_usage_note(str(event.payload.get("text", "")))
        if parsed is None:
            continue
        records.append(
            UsageRecord(
                agent=str(event.payload.get("author", "")),
                model=str(parsed["model"]),
                task_id=str(event.payload.get("task_id", "")),
                calls=int(parsed["calls"]),
                input_tokens=int(parsed["input_tokens"]),
                output_tokens=int(parsed["output_tokens"]),
                recorded_cost=parsed.get("cost"),
                seq=event.seq,
                ts=event.ts,
            )
        )
    return tuple(records)


def _summaries(
    records: Sequence[UsageRecord],
    pricing: Mapping[str, ModelPrice] | None,
    *,
    by_agent: bool,
) -> tuple[UsageSummary, ...]:
    """Aggregate records by agent identity or by model id."""
    calls: dict[str, int] = {}
    input_tokens: dict[str, int] = {}
    output_tokens: dict[str, int] = {}
    cost: dict[str, float] = {}
    counterparts: dict[str, set[str]] = {}
    for record in records:
        key = record.agent if by_agent else record.model
        other = record.model if by_agent else record.agent
        calls[key] = calls.get(key, 0) + record.calls
        input_tokens[key] = input_tokens.get(key, 0) + record.input_tokens
        output_tokens[key] = output_tokens.get(key, 0) + record.output_tokens
        cost[key] = cost.get(key, 0.0) + record.estimated_cost(pricing)
        counterparts.setdefault(key, set()).add(other)
    return tuple(
        UsageSummary(
            key=key,
            calls=calls[key],
            input_tokens=input_tokens[key],
            output_tokens=output_tokens[key],
            estimated_cost=cost[key],
            counterparts=tuple(sorted(value for value in counterparts[key] if value)),
        )
        for key in sorted(calls)
    )


def _budget_statuses(
    budgets: Mapping[str, float] | None,
    spend_by_agent: Mapping[str, float],
) -> tuple[BudgetStatus, ...]:
    """Return budget evidence for every declared agent ceiling."""
    if not budgets:
        return ()
    return tuple(
        BudgetStatus(
            agent=agent, budget=float(budgets[agent]), spent=spend_by_agent.get(agent, 0.0)
        )
        for agent in sorted(budgets)
    )


def _totals(
    records: Sequence[UsageRecord],
    pricing: Mapping[str, ModelPrice] | None,
) -> AccountingTotals:
    """Return fleet-wide totals across every record."""
    return AccountingTotals(
        calls=sum(record.calls for record in records),
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
        estimated_cost=sum(record.estimated_cost(pricing) for record in records),
    )


def _coerce_int(value: str | None, *, default: int) -> int:
    """Return a non-negative integer parsed from ``value`` or ``default``."""
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _coerce_float(value: str | None) -> float | None:
    """Return a non-negative float parsed from ``value`` or ``None``."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0.0 else None


def _summary_to_json(summary: UsageSummary) -> dict[str, object]:
    """Convert a usage summary into JSON-compatible fields."""
    return {
        "key": summary.key,
        "calls": summary.calls,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "total_tokens": summary.total_tokens,
        "estimated_cost": summary.estimated_cost,
        "counterparts": list(summary.counterparts),
    }


def _budget_to_json(status: BudgetStatus) -> dict[str, object]:
    """Convert budget evidence into JSON-compatible fields."""
    return {
        "agent": status.agent,
        "budget": status.budget,
        "spent": status.spent,
        "remaining": status.remaining,
        "over_budget": status.over_budget,
    }


def _record_to_json(record: UsageRecord, report: AccountingReport) -> dict[str, object]:
    """Convert a usage record into JSON-compatible fields."""
    del report
    return {
        "agent": record.agent,
        "model": record.model,
        "task_id": record.task_id,
        "calls": record.calls,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "recorded_cost": record.recorded_cost,
        "total_tokens": record.total_tokens,
        "seq": record.seq,
        "ts": record.ts,
    }


def _totals_to_json(totals: AccountingTotals) -> dict[str, object]:
    """Convert fleet totals into JSON-compatible fields."""
    return {
        "calls": totals.calls,
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "total_tokens": totals.total_tokens,
        "estimated_cost": totals.estimated_cost,
    }


def _render_summary(summary: UsageSummary) -> str:
    """Render one usage summary row."""
    return (
        f"- {terminal_text(summary.key)}: calls={summary.calls} tokens={summary.total_tokens} "
        f"(in={summary.input_tokens} out={summary.output_tokens}) "
        f"est_cost={summary.estimated_cost:.4f}"
    )


def _render_budget(status: BudgetStatus) -> str:
    """Render one budget evidence row."""
    flag = " OVER" if status.over_budget else ""
    return (
        f"- {terminal_text(status.agent)}: spent={status.spent:.4f} "
        f"budget={status.budget:.4f} "
        f"remaining={status.remaining:.4f}{flag}"
    )
