# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — model cost and token accounting CLI commands
"""CLI wrappers for opt-in model cost/token accounting.

``synapse accounting record`` posts one opt-in usage note onto the shared
progress ledger; ``synapse accounting report`` reads a hub SQLite event store
back and prints aggregated per-agent and per-model usage with optional pricing
and budget evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.accounting import (
    USAGE_NOTE_KIND,
    ModelPrice,
    accounting_to_json,
    format_usage_note,
    render_human,
    run_accounting_report,
)
from synapse_channel.waiter_identity import waiter_owner


def load_pricing_table(path: str | None) -> dict[str, ModelPrice] | None:
    """Load a per-model pricing table from a JSON file.

    Parameters
    ----------
    path : str or None
        Path to a JSON object mapping model id to ``{"input_per_1k", "output_per_1k"}``.

    Returns
    -------
    dict[str, ModelPrice] or None
        Parsed pricing table, or ``None`` when no path is given.

    Raises
    ------
    ValueError
        If the file is not a JSON object of numeric price pairs.
    """
    if path is None:
        return None
    raw = _read_json_object(path)
    pricing: dict[str, ModelPrice] = {}
    for model, value in raw.items():
        if not isinstance(value, dict):
            msg = f"pricing entry for {model!r} must be an object"
            raise ValueError(msg)
        pricing[model] = ModelPrice(
            input_per_1k=_as_float(value.get("input_per_1k", 0.0), context=f"{model}.input_per_1k"),
            output_per_1k=_as_float(
                value.get("output_per_1k", 0.0), context=f"{model}.output_per_1k"
            ),
        )
    return pricing


def _load_budgets(path: str | None) -> dict[str, float] | None:
    """Load a per-agent budget table from a JSON file.

    Parameters
    ----------
    path : str or None
        Path to a JSON object mapping agent identity to a numeric spend ceiling.

    Returns
    -------
    dict[str, float] or None
        Parsed budget table, or ``None`` when no path is given.

    Raises
    ------
    ValueError
        If the file is not a JSON object of numeric ceilings.
    """
    if path is None:
        return None
    raw = _read_json_object(path)
    return {agent: _as_float(value, context=agent) for agent, value in raw.items()}


def _read_json_object(path: str) -> dict[str, Any]:
    """Return a JSON object loaded from ``path``."""
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"could not read JSON file {path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return loaded


def _as_float(value: object, *, context: str) -> float:
    """Return ``value`` as a non-negative float or raise with ``context``."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        msg = f"{context} must be a non-negative number"
        raise ValueError(msg)
    return float(value)


def _cmd_report(args: argparse.Namespace) -> int:
    """Run one accounting report and print it."""
    try:
        pricing = load_pricing_table(args.pricing)
        budgets = _load_budgets(args.budget)
        report = run_accounting_report(
            args.db, pricing=pricing, budgets=budgets,
            key_file=getattr(args, "db_key_file", None),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(accounting_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0


async def _emit_usage(
    *,
    uri: str,
    name: str,
    task_id: str,
    note: str,
    token: str | None,
    ready_timeout: float,
) -> int:
    """Connect to the hub and post one opt-in usage progress note."""
    sender = waiter_owner(name)

    async def collect(_data: dict[str, Any]) -> None:
        return None

    agent = SynapseAgent(sender, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    sender,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                ),
                file=sys.stderr,
            )
            return 1
        if await closed_after_ready(agent):
            print(
                describe_connect_failure(
                    sender,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                ),
                file=sys.stderr,
            )
            return 1
        await agent.post_progress(task_id, note, kind=USAGE_NOTE_KIND)
        return 0
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _cmd_record(args: argparse.Namespace) -> int:
    """Build a canonical usage note and post it to the shared ledger."""
    try:
        note = format_usage_note(
            model=args.model,
            calls=args.calls,
            input_tokens=args.input_tokens,
            output_tokens=args.output_tokens,
            cost=args.cost,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return asyncio.run(
        _emit_usage(
            uri=args.uri,
            name=args.name,
            task_id=args.task,
            note=note,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``accounting`` command group."""
    parser = subparsers.add_parser(
        "accounting",
        help="Record and report opt-in model cost/token usage from the event log.",
    )
    group = parser.add_subparsers(dest="accounting_command", required=True)
    _add_report_parser(group)
    _add_record_parser(group)


def _add_report_parser(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``accounting report``."""
    report = group.add_parser(
        "report",
        help="Aggregate opt-in usage from a hub SQLite event store, e.g. ~/synapse/hub.db.",
    )
    report.add_argument("db", help="Path to the hub event store.")
    report.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    report.add_argument(
        "--pricing",
        default=None,
        help="JSON file mapping model -> {input_per_1k, output_per_1k} for cost estimates.",
    )
    report.add_argument(
        "--budget",
        default=None,
        help="JSON file mapping agent -> spend ceiling for budget evidence.",
    )
    report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    report.set_defaults(func=_cmd_report)


def _add_record_parser(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``accounting record``."""
    record = group.add_parser(
        "record",
        help="Post one opt-in usage note to the shared progress ledger.",
    )
    record.add_argument("--uri", default=default_hub_uri(), help="Hub URI.")
    record.add_argument("--name", required=True, help="Recording agent identity.")
    record.add_argument("--task", default="", help="Task id the usage is recorded against.")
    record.add_argument("--model", required=True, help="Model id the usage is attributed to.")
    record.add_argument("--calls", type=int, default=1, help="Number of model calls.")
    record.add_argument("--input-tokens", type=int, default=0, help="Input/prompt tokens consumed.")
    record.add_argument(
        "--output-tokens", type=int, default=0, help="Output/completion tokens produced."
    )
    record.add_argument(
        "--cost", type=float, default=None, help="Optional recorder-supplied cost for the calls."
    )
    record.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    record.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    record.set_defaults(func=_cmd_record)
