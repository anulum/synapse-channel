# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deliberation subcommands of the participant CLI surface
"""``synapse participant exchange`` and ``convene`` — multi-party deliberations.

Where ``participant ask`` runs one turn against one provider, these two subcommands
drive the Fabric's deliberation layers from the same operator surface:
``exchange`` mirrors :func:`~synapse_channel.participants.exchange.conduct_exchange`
(one participant answers, a second reacts to that answer as fenced peer data), and
``convene`` mirrors :func:`~synapse_channel.participants.convene.convene` (an opening
fan-out, the mode's cross-critique rounds, and — in a symposium — a moderator's
synthesis). Each panel member is named as ``PROVIDER[:MODEL]``, so a mixed panel such
as ``claude codex ollama:gemma3:1b`` costs one command line.

Both commands print each turn as it is produced (the injected result sink is the
CLI's stdout), or the full typed transcript with ``--json``. Exit codes follow
``ask``: ``0`` when every turn answered and the run completed, ``1`` when a provider
was unavailable, any turn errored or abstained, or a ``--budget-usd`` ceiling halted
the convocation, ``2`` for a refused configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from synapse_channel.cli_accounting import load_pricing_table
from synapse_channel.cli_participants import (
    DEFAULT_ASK_TIMEOUT,
    build_participant,
    refusal_for,
)
from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.convene import ConvocationTranscript, convene
from synapse_channel.participants.conversation import STOPPED_BUDGET
from synapse_channel.participants.envelope import TurnResult
from synapse_channel.participants.exchange import ExchangeTranscript, conduct_exchange
from synapse_channel.participants.modes import ConversationMode, policy_for, select_mode
from synapse_channel.participants.participant import Participant

MODE_AUTO = "auto"
"""``--mode`` value asking :func:`~synapse_channel.participants.modes.select_mode` to choose."""

SPEC_METAVAR = "PROVIDER[:MODEL]"
"""How a panel member is named on the command line; the model part may itself hold colons."""


def parse_spec(spec: str) -> tuple[str, str]:
    """Split a ``PROVIDER[:MODEL]`` spec into its provider key and model.

    Only the first colon separates the two, so a model name that itself contains
    colons (``ollama:gemma3:1b``) survives intact.

    Raises
    ------
    ValueError
        When the provider part is empty.
    """
    provider, _, model = spec.partition(":")
    if not provider:
        msg = f"empty provider in participant spec {spec!r}; expected {SPEC_METAVAR}"
        raise ValueError(msg)
    return provider, model


def build_deliberants(specs: Sequence[str], *, timeout: float) -> list[Participant]:
    """Construct one participant per spec, numbering repeated providers.

    A provider may appear several times on one panel (two independent ``claude``
    seats, say); the second and later occurrences get ``-2``, ``-3`` … identity
    suffixes so every turn result names a distinct seat.

    Raises
    ------
    ValueError
        For a refused provider (grok while its stream schema is unverified), an
        unknown provider, an empty provider part, or a model-less turn request to
        a provider whose driver has no default model.
    """
    seats: list[Participant] = []
    seen: dict[str, int] = {}
    for spec in specs:
        provider, model = parse_spec(spec)
        refusal = refusal_for(provider)
        if refusal is not None:
            raise ValueError(refusal)
        seen[provider] = seen.get(provider, 0) + 1
        identity = f"participant/{provider}"
        if seen[provider] > 1:
            identity = f"{identity}-{seen[provider]}"
        seats.append(build_participant(provider, identity=identity, model=model, timeout=timeout))
    return seats


def _report_unavailable(participants: Sequence[Participant]) -> bool:
    """Print every unavailable seat's health detail; return whether any was."""
    unavailable = False
    for participant in participants:
        health = participant.health()
        if not health.available:
            print(f"{health.identity} is unavailable: {health.detail}")
            unavailable = True
    return unavailable


def _print_turn(result: TurnResult) -> None:
    """Print one turn as an identity-labelled block, faithful about degraded turns."""
    if result["is_error"]:
        print(f"[{result['participant']}] errored: {result['reason']}")
    elif result["abstained"]:
        print(f"[{result['participant']}] abstained: {result['reason']}")
    else:
        print(f"[{result['participant']}] {result['answer']}")


def _printing_sink(
    marker: Callable[[int], str | None],
) -> Callable[[TurnResult], Awaitable[None]]:
    """Build a result sink that prints each turn, with ``marker(index)`` headings."""
    count = 0

    async def post(result: TurnResult) -> None:
        nonlocal count
        heading = marker(count)
        if heading is not None:
            print(heading)
        _print_turn(result)
        count += 1

    return post


async def _silent_sink(result: TurnResult) -> None:
    """Swallow live results; ``--json`` renders the transcript once, at the end."""


def _degraded(results: Sequence[TurnResult]) -> bool:
    """Return whether any turn errored or abstained."""
    return any(result["is_error"] or result["abstained"] for result in results)


def _topic_or_fresh(topic: str | None) -> str:
    """Return the correlation topic, minting a fresh CLI one when none was given."""
    return topic or f"participant-cli-{uuid.uuid4().hex[:8]}"


def _exchange_payload(transcript: ExchangeTranscript) -> dict[str, object]:
    """Render an exchange transcript as its JSON wire shape."""
    return {
        "topic_id": transcript.topic_id,
        "question": transcript.question,
        "turns": list(transcript.turns),
    }


def _convocation_payload(transcript: ConvocationTranscript) -> dict[str, object]:
    """Render a convocation transcript as its JSON wire shape."""
    return {
        "mode": transcript.mode.value,
        "question": transcript.question,
        "rounds": [list(round_results) for round_results in transcript.rounds],
        "synthesis": transcript.synthesis,
        "total_cost_usd": transcript.total_cost_usd,
        "stopped": transcript.stopped,
    }


def _cmd_exchange(args: argparse.Namespace) -> int:
    """Run an opener turn and a reacting turn, printing both.

    The reactor sees the opener's result only as fenced peer data — the CLI
    inherits the exchange layer's injection boundary rather than re-implementing
    it. Exit ``0`` when both turns answered, ``1`` for an unavailable provider or
    a degraded turn, ``2`` for a refused configuration.
    """
    try:
        opener, reactor = build_deliberants([args.opener, args.reactor], timeout=args.timeout)
    except ValueError as exc:
        print(str(exc))
        return 2
    if _report_unavailable((opener, reactor)):
        return 1

    def marker(index: int) -> str | None:
        return ("— opener —", "— reactor —")[index]

    sink = _silent_sink if args.json else _printing_sink(marker)
    transcript = asyncio.run(
        conduct_exchange(
            args.question,
            opener,
            reactor,
            topic_id=_topic_or_fresh(args.topic),
            post=sink,
            shared_context=args.context,
        )
    )
    if args.json:
        print(json.dumps(_exchange_payload(transcript), sort_keys=True))
    return 1 if _degraded(transcript.turns) else 0


def _resolve_mode(mode: str, panel_size: int, *, moderator_available: bool) -> ConversationMode:
    """Return the requested mode, or let the session shape choose under ``auto``."""
    if mode == MODE_AUTO:
        return select_mode(panel_size, moderator_available=moderator_available)
    return ConversationMode(mode)


def _convene_marker(panel_size: int, synthesis_index: int | None) -> Callable[[int], str | None]:
    """Head each round's first turn with its number, and the synthesis turn distinctly."""

    def marker(index: int) -> str | None:
        if index == synthesis_index:
            return "— synthesis —"
        if index % panel_size == 0:
            return f"— round {index // panel_size + 1} —"
        return None

    return marker


@dataclass(frozen=True)
class _SeatPlan:
    """One seat's dry-run standing: identity, readiness, planned turns, cost.

    The cost is ``turns`` times the seat model's per-turn price; a seat whose
    model has no line in the pricing table carries a ``None`` estimate rather
    than a fabricated zero.
    """

    identity: str
    provider: str
    model: str
    available: bool
    detail: str
    turns: int
    estimated_cost_usd: float | None

    def to_json(self) -> dict[str, object]:
        """Return the seat plan's JSON wire shape."""
        return {
            "identity": self.identity,
            "provider": self.provider,
            "model": self.model,
            "available": self.available,
            "detail": self.detail,
            "turns": self.turns,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def _seat_plan(
    spec: str,
    seat: Participant,
    *,
    turns: int,
    pricing: dict[str, ModelPrice] | None,
    input_tokens: int,
    output_tokens: int,
) -> _SeatPlan:
    """Describe one seat's dry-run standing without taking a turn."""
    provider, model = parse_spec(spec)
    health = seat.health()
    price = (pricing or {}).get(model)
    estimated = None if price is None else turns * price.estimate(input_tokens, output_tokens)
    return _SeatPlan(
        identity=health.identity,
        provider=provider,
        model=model,
        available=health.available,
        detail=health.detail,
        turns=turns,
        estimated_cost_usd=estimated,
    )


def _render_seat_plan(plan: _SeatPlan) -> str:
    """Render one seat's dry-run line."""
    readiness = "ready" if plan.available else f"UNAVAILABLE ({plan.detail})"
    priced = "unpriced" if plan.estimated_cost_usd is None else f"~${plan.estimated_cost_usd:.4f}"
    model = f" model={plan.model}" if plan.model else ""
    return f"- {plan.identity}{model}: {plan.turns} turn(s), {priced}, {readiness}"


def _dry_run_report(
    args: argparse.Namespace,
    specs: Sequence[str],
    seats: Sequence[Participant],
    moderator: Participant | None,
) -> int:
    """Print the convocation plan — seats, mode, rounds, cost — without a turn.

    Health probes run (they never take a turn), so the report doubles as a
    pre-flight: exit ``0`` when every seat is ready, ``1`` when any seat is
    unavailable, ``2`` for a refused configuration or an unreadable pricing
    file. Costs come from the operator's ``--pricing`` table under the printed
    per-turn token assumptions; seats without a price line stay unpriced and
    are excluded from the total rather than counted as free.
    """
    mode = _resolve_mode(args.mode, len(seats), moderator_available=moderator is not None)
    policy = policy_for(mode)
    if policy.uses_moderator and moderator is None:
        print(f"{mode.value} requires a moderator participant")
        return 2
    try:
        pricing = load_pricing_table(args.pricing)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rounds = policy.critique_rounds + 1
    plans = [
        _seat_plan(
            spec,
            seat,
            turns=rounds,
            pricing=pricing,
            input_tokens=args.est_input_tokens,
            output_tokens=args.est_output_tokens,
        )
        for spec, seat in zip(specs, seats, strict=False)
    ]
    if moderator is not None and policy.uses_moderator:
        plans.append(
            _seat_plan(
                specs[-1],
                moderator,
                turns=1,
                pricing=pricing,
                input_tokens=args.est_input_tokens,
                output_tokens=args.est_output_tokens,
            )
        )
    priced = [plan.estimated_cost_usd for plan in plans if plan.estimated_cost_usd is not None]
    total = sum(priced) if priced else None
    unpriced = len(plans) - len(priced)
    total_turns = sum(plan.turns for plan in plans)
    exceeded = None
    if args.budget_usd is not None and total is not None:
        exceeded = total > args.budget_usd
    payload: dict[str, object] = {
        "mode": mode.value,
        "critique_rounds": policy.critique_rounds,
        "uses_moderator": policy.uses_moderator,
        "turns": total_turns,
        "seats": [plan.to_json() for plan in plans],
        "estimated_total_usd": total,
        "unpriced_seats": unpriced,
        "assumptions": {
            "input_tokens_per_turn": args.est_input_tokens,
            "output_tokens_per_turn": args.est_output_tokens,
        },
        "budget_usd": args.budget_usd,
        "budget_exceeded": exceeded,
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"dry run: mode={mode.value} · rounds={rounds}"
            f"{' + synthesis' if policy.uses_moderator else ''} · turns={total_turns}"
        )
        for plan in plans:
            print(_render_seat_plan(plan))
        if total is None:
            print("estimated total: unpriced (pass --pricing to estimate costs)")
        else:
            suffix = f" ({unpriced} seat(s) unpriced, excluded)" if unpriced else ""
            print(
                f"estimated total: ~${total:.4f} assuming "
                f"{args.est_input_tokens} in / {args.est_output_tokens} out tokens per turn"
                f"{suffix}"
            )
        if exceeded is not None:
            verdict = "EXCEEDS" if exceeded else "fits within"
            print(f"budget: estimate {verdict} --budget-usd {args.budget_usd:.4f}")
    return 1 if any(not plan.available for plan in plans) else 0


def _cmd_convene(args: argparse.Namespace) -> int:
    """Convene the named panel in a conversation mode and print every turn.

    Under ``--mode auto`` the mode follows the session shape (panel size and
    whether ``--moderator`` was given), which is the dynamic selection the modes
    exist for. ``--dry-run`` prints the plan — resolved mode, rounds, per-seat
    readiness and estimated cost — without taking any turn. Exit ``0`` when the
    convocation completed with every turn answered, ``1`` for an unavailable
    seat, any degraded turn, or a ``--budget-usd`` halt, ``2`` for a refused
    configuration (including a symposium without a moderator).
    """
    specs = list(args.panel) + ([args.moderator] if args.moderator else [])
    try:
        seats = build_deliberants(specs, timeout=args.timeout)
    except ValueError as exc:
        print(str(exc))
        return 2
    moderator = seats.pop() if args.moderator else None
    if args.dry_run:
        return _dry_run_report(args, specs, seats, moderator)
    if _report_unavailable(seats + ([moderator] if moderator else [])):
        return 1

    try:
        mode = _resolve_mode(args.mode, len(seats), moderator_available=moderator is not None)
        policy = policy_for(mode)
        synthesis_index = (
            (policy.critique_rounds + 1) * len(seats) if policy.uses_moderator else None
        )
        sink: Callable[[TurnResult], Awaitable[None]] = _silent_sink
        if not args.json:
            sink = _printing_sink(_convene_marker(len(seats), synthesis_index))
        transcript = asyncio.run(
            convene(
                args.question,
                seats,
                mode=mode,
                topic_id=_topic_or_fresh(args.topic),
                post=sink,
                shared_context=args.context,
                moderator=moderator,
                budget_usd=args.budget_usd,
            )
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.json:
        print(json.dumps(_convocation_payload(transcript), sort_keys=True))
    else:
        turns = sum(len(round_results) for round_results in transcript.rounds)
        turns += 1 if transcript.synthesis is not None else 0
        print(
            f"mode={transcript.mode.value} · stopped={transcript.stopped} · "
            f"turns={turns} · cost=${transcript.total_cost_usd:.4f}"
        )
    all_turns = [result for round_results in transcript.rounds for result in round_results]
    if transcript.synthesis is not None:
        all_turns.append(transcript.synthesis)
    degraded = _degraded(all_turns) or transcript.stopped == STOPPED_BUDGET
    return 1 if degraded else 0


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the framing options every deliberation shares with ``ask``."""
    parser.add_argument(
        "--context",
        default="",
        help="Shared framing prepended to every seat's context (never the user prompt).",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Topic id correlating every turn; defaults to a fresh participant-cli id.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_ASK_TIMEOUT,
        help="Seconds any one turn may take before its driver reports an error result.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the full typed transcript as JSON."
    )


def add_parsers(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``exchange`` and ``convene`` on the ``participant`` command group."""
    exchange = group.add_parser(
        "exchange",
        help="One provider answers, a second reviews that answer as fenced data.",
    )
    exchange.add_argument("question", help="The question both participants answer.")
    exchange.add_argument("opener", metavar=SPEC_METAVAR, help="Seat that answers first.")
    exchange.add_argument(
        "reactor",
        metavar=SPEC_METAVAR,
        help="Seat that answers second, having seen the opener's result as data.",
    )
    _add_shared_arguments(exchange)
    exchange.set_defaults(func=_cmd_exchange)

    convener = group.add_parser(
        "convene",
        help="Fan a question out to a panel, run the mode's critique rounds, synthesise.",
    )
    convener.add_argument("question", help="The question put to every panel seat.")
    convener.add_argument(
        "panel", nargs="+", metavar=SPEC_METAVAR, help="The panel, one seat per spec."
    )
    convener.add_argument(
        "--mode",
        default=MODE_AUTO,
        choices=[MODE_AUTO, *(mode.value for mode in ConversationMode)],
        help="Conversation shape; auto selects from panel size and moderator presence.",
    )
    convener.add_argument(
        "--moderator",
        default=None,
        metavar=SPEC_METAVAR,
        help="Seat that synthesises the final answer; required by a symposium.",
    )
    convener.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="Cumulative cost ceiling checked between rounds and before synthesis.",
    )
    convener.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan (seats, mode, rounds, estimated cost) without taking a turn.",
    )
    convener.add_argument(
        "--pricing",
        default=None,
        help="JSON file mapping model -> {input_per_1k, output_per_1k} for --dry-run estimates.",
    )
    convener.add_argument(
        "--est-input-tokens",
        type=int,
        default=1000,
        help="Assumed input tokens per turn for --dry-run cost estimates.",
    )
    convener.add_argument(
        "--est-output-tokens",
        type=int,
        default=500,
        help="Assumed output tokens per turn for --dry-run cost estimates.",
    )
    _add_shared_arguments(convener)
    convener.set_defaults(func=_cmd_convene)
