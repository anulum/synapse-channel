# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI adapter for drafting a git claim from natural language
"""Thin CLI adapter for :mod:`synapse_channel.participants.claim_parse`.

``synapse claim-parse --from-text "…"`` drives the operator's configured provider to
PROPOSE a structured git claim and prints the exact ``synapse git-claim`` command that
would create it. This adapter never calls the hub or executes the proposed command.
The selected provider retains its own tool, credential and filesystem permissions;
this adapter is not a sandbox and prompt framing cannot enforce provider behaviour.
Provider selection and proposed owner identity are explicit. Invalid responses fail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Callable

from synapse_channel.cli_participants import (
    DEFAULT_ASK_TIMEOUT,
    PROVIDERS,
    build_participant,
    refusal_for,
)
from synapse_channel.participants.api_ollama import OllamaApiParticipant
from synapse_channel.participants.claim_parse import (
    ProposalError,
    ProviderInvoke,
    build_parse_prompt,
    proposal_to_payload,
    propose_claim,
    render_proposal,
)
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.participant import Participant
from synapse_channel.terminal_text import terminal_text

_CLAIM_PARSE_TOPIC = "claim-parse"
"""Local correlation id for the one-shot turn; the hub is never involved."""

ParticipantBuilder = Callable[..., Participant]


def _operator_value(value: str) -> str:
    """Reject terminal controls and oversized operator fields before any provider call."""
    if not value.strip() or len(value) > 200 or not value.isprintable():
        raise argparse.ArgumentTypeError(
            "value must be printable nonempty text up to 200 characters"
        )
    return value


def _positive_timeout(value: str) -> float:
    """Parse a strictly positive turn timeout in seconds for argparse."""
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and greater than zero")
    return timeout


def _answer_from_result(result: TurnResult, *, provider: str) -> str:
    """Return the provider's answer text or raise ``ProposalError`` on a bad turn.

    A failed turn and an abstention are both fatal for a draft: there is nothing safe
    to propose, so the caller stops rather than inventing a claim.
    """
    if result["is_error"]:
        reason = result["reason"] or "unknown error"
        raise ProposalError(f"provider {provider!r} failed: {reason}")
    if result["abstained"]:
        raise ProposalError(f"provider {provider!r} produced no proposal")
    return result["answer"]


def build_provider_invoke(
    provider: str,
    *,
    identity: str,
    model: str,
    timeout: float,
    endpoint: str | None = None,
    participant_builder: ParticipantBuilder = build_participant,
) -> ProviderInvoke:
    """Return a one-shot provider call for :func:`propose_claim`.

    Honours the same turn refusals as ``participant ask`` (a provider whose stream
    schema is unverified is refused here too), and surfaces a model-less provider as a
    clean :class:`ProposalError` rather than a raw ``ValueError``.
    """
    refusal = refusal_for(provider)
    if refusal is not None:
        raise ProposalError(refusal)
    participant: Participant
    try:
        if endpoint is not None:
            if provider != "ollama-api" or not model:
                raise ValueError("--endpoint requires --provider=ollama-api and --model")
            participant = OllamaApiParticipant(
                identity, model=model, timeout=timeout, endpoint=endpoint
            )
        else:
            participant = participant_builder(
                provider, identity=identity, model=model, timeout=timeout
            )
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc

    def _invoke(prompt: str) -> str:
        """Request one provider turn and refuse failed or abstained output."""
        request = TurnRequest(topic_id=_CLAIM_PARSE_TOPIC, prompt=prompt)
        result = asyncio.run(participant.take_turn(request))
        return _answer_from_result(result, provider=provider)

    return _invoke


def _cmd_claim_parse(
    args: argparse.Namespace,
    *,
    invoke_factory: Callable[..., ProviderInvoke] = build_provider_invoke,
) -> int:
    """Draft a claim from ``--from-text`` and print it; never submit it."""
    try:
        build_parse_prompt(args.from_text)
        invoke = invoke_factory(
            args.provider,
            identity=args.name,
            model=args.model,
            timeout=args.timeout,
            endpoint=args.endpoint,
        )
        proposal = propose_claim(args.from_text, invoke=invoke)
    except ProposalError as exc:
        print(f"claim-parse: {terminal_text(exc)}", file=sys.stderr)
        return 1
    if args.json:
        payload = proposal_to_payload(proposal, name=args.name, uri=args.uri)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_proposal(proposal, name=args.name, uri=args.uri))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the opt-in ``claim-parse`` command."""
    parser = subparsers.add_parser(
        "claim-parse",
        help=(
            "Draft a git-claim from a natural-language description using a configured "
            "AI provider (opt-in). Prints a proposal and the exact command to run; it "
            "never submits a claim."
        ),
    )
    parser.add_argument(
        "--from-text",
        required=True,
        metavar="TEXT",
        help="Natural-language description of the work you intend to start.",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        required=True,
        help="Explicit provider opt-in; its configured tools, credentials and costs still apply.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model override; required for providers whose driver has no default model.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=DEFAULT_ASK_TIMEOUT,
        help="Seconds the provider turn may take.",
    )
    parser.add_argument(
        "--endpoint",
        type=_operator_value,
        help="Explicit Ollama generate URL; only with --provider=ollama-api and --model.",
    )
    parser.add_argument(
        "--name",
        type=_operator_value,
        required=True,
        help="Claim owner identity written into the proposed command.",
    )
    parser.add_argument(
        "--uri",
        type=_operator_value,
        default=None,
        help="Hub URI to write into the proposed command; omit to use the client default.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the proposal as machine-readable JSON (still never submits).",
    )
    parser.set_defaults(func=_cmd_claim_parse)
