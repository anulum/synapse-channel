# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI surface over the Participant Fabric providers
"""``synapse participant`` — probe and drive Participant Fabric providers.

The Participant Fabric presents each provider CLI or API as a uniform
:class:`~synapse_channel.participants.participant.Participant`; until now it was
reachable only as a library import. This command group is its operator surface:
``participant list`` reports every registered provider's readiness snapshot
(:class:`~synapse_channel.participants.participant.ParticipantHealth`), and
``participant ask`` runs exactly one
:class:`~synapse_channel.participants.envelope.TurnRequest` against one provider and
prints the answer — or the full typed
:class:`~synapse_channel.participants.envelope.TurnResult` with ``--json``.

Grok is registered but refused for turns while
:data:`~synapse_channel.participants.grok_stream.GROK_SCHEMA_VERIFIED` is false:
its stream schema is modelled on documentation, not verified against the real
CLI, and driving an unverified schema silently would fabricate confidence.
Gemini is gated the same way on
:data:`~synapse_channel.participants.gemini_stream.GEMINI_SCHEMA_VERIFIED`: its
event shape is read from the installed 0.47.0 bundle source but no behavioural
capture exists yet, so turns stay refused rather than risk a silent misparse.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Callable

from synapse_channel.cli_participants_memory import (
    add_memory_arguments,
    wrap_participants,
)
from synapse_channel.participants.api_ollama import OllamaApiParticipant
from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.gemini_stream import GEMINI_SCHEMA_VERIFIED
from synapse_channel.participants.grok_stream import GROK_SCHEMA_VERIFIED
from synapse_channel.participants.headless_claude import HeadlessClaudeParticipant
from synapse_channel.participants.headless_codex import CodexParticipant
from synapse_channel.participants.headless_gemini import GeminiParticipant
from synapse_channel.participants.headless_grok import GrokParticipant
from synapse_channel.participants.headless_kimi import KimiParticipant
from synapse_channel.participants.headless_ollama import OllamaParticipant
from synapse_channel.participants.participant import Participant

ParticipantBuilder = Callable[..., Participant]

DEFAULT_ASK_TIMEOUT = 600.0

_MODEL_REQUIRED = frozenset({"ollama", "ollama-api"})
"""Providers whose driver has no configured default model, so ``--model`` is mandatory."""

PROVIDERS: dict[str, ParticipantBuilder] = {
    "claude": HeadlessClaudeParticipant,
    "codex": CodexParticipant,
    "gemini": GeminiParticipant,
    "kimi": KimiParticipant,
    "ollama": OllamaParticipant,
    "ollama-api": OllamaApiParticipant,
    "grok": GrokParticipant,
}
"""Registered provider drivers, keyed by the name the operator selects."""

_GROK_REFUSAL = (
    "grok turns are disabled: GROK_SCHEMA_VERIFIED=False (stream schema not captured "
    "from a real stable run; prior CLI issues resolved). A turn could silently "
    "misparse. Use another provider, or verify the schema first."
)

_GEMINI_REFUSAL = (
    "gemini turns are disabled: GEMINI_SCHEMA_VERIFIED=False (stream schema read from "
    "the installed 0.47.0 bundle source, not captured from a real run; OAuth-personal "
    "accounts also fail CLI setup with IneligibleTierError). A turn could silently "
    "misparse. Use another provider, or capture and verify the schema first."
)


def refusal_for(provider: str) -> str | None:
    """Return why ``provider`` must not take turns right now, or ``None`` when it may.

    ``ask`` and the deliberation subcommands share this gate so a provider refused
    solo is refused on a panel for the same stated reason.
    """
    if provider == "grok" and not GROK_SCHEMA_VERIFIED:
        return _GROK_REFUSAL
    if provider == "gemini" and not GEMINI_SCHEMA_VERIFIED:
        return _GEMINI_REFUSAL
    return None


def build_participant(
    provider: str,
    *,
    identity: str,
    model: str,
    timeout: float,
    probe: bool = False,
) -> Participant:
    """Construct the named provider's participant.

    Parameters
    ----------
    provider : str
        Key in :data:`PROVIDERS`.
    identity : str
        Bus identity the participant reports in its health and results.
    model : str
        Model override; required for the providers in :data:`_MODEL_REQUIRED`
        (their drivers configure no default) unless probing, optional elsewhere.
    timeout : float
        Seconds one turn may take.
    probe : bool, optional
        A health probe never takes a turn, so it skips the model requirement —
        binary presence is checkable without knowing which model would run.

    Raises
    ------
    ValueError
        For an unknown provider, or a model-less *turn* request to a provider
        whose driver has no default model.
    """
    builder = PROVIDERS.get(provider)
    if builder is None:
        known = ", ".join(sorted(PROVIDERS))
        msg = f"unknown provider {provider!r}; known providers: {known}"
        raise ValueError(msg)
    if not probe and provider in _MODEL_REQUIRED and not model:
        msg = f"provider {provider!r} has no default model; pass --model"
        raise ValueError(msg)
    return builder(identity, model=model, timeout=timeout)


def _default_identity(provider: str) -> str:
    """Return the identity a CLI-driven participant reports by default."""
    return f"participant/{provider}"


def _cmd_list(args: argparse.Namespace) -> int:
    """Report every registered provider's readiness snapshot.

    Each provider is constructed with a throwaway identity and asked for its
    :meth:`health` — a probe, never a turn. A provider whose turns are refused
    (see :func:`refusal_for`) carries the schema caveat on its line so the roster
    never over-promises. Exit code ``0`` regardless of availability: this is a
    report, not a gate.
    """
    healths = []
    for provider in sorted(PROVIDERS):
        participant = build_participant(
            provider,
            identity=_default_identity(provider),
            model=args.model,
            timeout=DEFAULT_ASK_TIMEOUT,
            probe=True,
        )
        health = participant.health()
        note = ""
        if refusal_for(provider) is not None:
            note = " [turns disabled: stream schema unverified]"
        healths.append((provider, health, note))
    if args.json:
        payload = [
            {
                "provider": provider,
                "identity": health.identity,
                "channel": str(health.channel.value),
                "available": health.available,
                "detail": health.detail + note,
            }
            for provider, health, note in healths
        ]
        print(json.dumps(payload, sort_keys=True))
        return 0
    print(f"Participant providers ({len(healths)}):")
    for provider, health, note in healths:
        state = "available" if health.available else "unavailable"
        print(f"  {provider} [{health.channel.value}] {state}: {health.detail}{note}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    """Run one turn against one provider and print its outcome.

    The prompt travels as the turn's ``prompt`` and ``--context`` as its
    system-level framing, exactly as the conversation layers pass them, so a
    CLI turn behaves like a bus turn. Exit ``0`` for an answer, ``1`` when the
    provider is unavailable or the turn errored or abstained, ``2`` for a
    refused configuration (unknown provider, missing required model, grok).
    """
    refusal = refusal_for(args.provider)
    if refusal is not None:
        print(refusal)
        return 2
    identity = args.identity or _default_identity(args.provider)
    try:
        participant = build_participant(
            args.provider,
            identity=identity,
            model=args.model,
            timeout=args.timeout,
        )
        participant = wrap_participants([participant], args)[0]
    except ValueError as exc:
        print(str(exc))
        return 2
    health = participant.health()
    if not health.available:
        print(f"{args.provider} is unavailable: {health.detail}")
        return 1
    request = TurnRequest(
        topic_id=args.topic or f"participant-cli-{uuid.uuid4().hex[:8]}",
        prompt=args.prompt,
        context=args.context,
        model=args.model,
    )
    result = asyncio.run(participant.take_turn(request))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["is_error"] or result["abstained"]:
        verdict = "errored" if result["is_error"] else "abstained"
        print(f"{args.provider} {verdict}: {result['reason']}")
    else:
        print(result["answer"])
    return 1 if result["is_error"] or result["abstained"] else 0


def add_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    """Register the ``participant`` command group; return it for further subcommands.

    The deliberation subcommands (``exchange``, ``convene``) live in
    :mod:`synapse_channel.cli_participants_deliberate`, which builds on this
    module's registry; returning the group lets the CLI wire them onto it without
    an import cycle.
    """
    parser = subparsers.add_parser(
        "participant",
        help="Probe or drive Participant Fabric providers (claude, codex, kimi, ollama, …).",
    )
    group = parser.add_subparsers(dest="participant_command", required=True)

    lister = group.add_parser(
        "list", help="Report each registered provider's readiness without taking a turn."
    )
    lister.add_argument(
        "--model", default="", help="Model recorded on the probe identities (informational)."
    )
    lister.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    lister.set_defaults(func=_cmd_list)

    ask = group.add_parser("ask", help="Run one turn against one provider and print the answer.")
    ask.add_argument("provider", help="Provider to drive: " + ", ".join(sorted(PROVIDERS)) + ".")
    ask.add_argument("prompt", help="The question or instruction for this turn.")
    ask.add_argument(
        "--model",
        default="",
        help="Model for this turn; required for ollama and ollama-api (no driver default).",
    )
    ask.add_argument(
        "--context",
        default="",
        help="System-level framing injected alongside the prompt (never the user prompt).",
    )
    ask.add_argument(
        "--identity",
        default=None,
        help="Bus identity stamped on the result; defaults to participant/<provider>.",
    )
    ask.add_argument(
        "--topic",
        default=None,
        help="Topic id correlating this turn; defaults to a fresh participant-cli id.",
    )
    ask.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_ASK_TIMEOUT,
        help="Seconds the turn may take before the driver reports an error result.",
    )
    add_memory_arguments(ask)
    ask.add_argument("--json", action="store_true", help="Print the full TurnResult as JSON.")
    ask.set_defaults(func=_cmd_ask)
    return group
