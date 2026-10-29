# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — work-state command
"""``synapse work-state build|verify``: hand over checkable state, not authority.

``build`` writes a ``synapse.work-state.v1`` packet from the current state of
the named inputs and repositories, refusing to overwrite an existing file.
``verify`` checks a packet against the world now and prints each claim's
standing. It exits 0 when every input and artefact is unchanged, 1 when one
has changed or gone, and 2 on an unusable packet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from synapse_channel.work_state import WorkStateError, build_packet, load_packet, verify_packet


def _build(args: argparse.Namespace) -> int:
    """Write a new packet from the named inputs, repositories and claims."""
    claims = [{"text": text, "origin": "agent_claim"} for text in args.agent_claim]
    claims += [{"text": text, "origin": "tool_result"} for text in args.tool_result]
    claims += [
        {"text": text, "origin": "verified_artefact", "subject": subject}
        for subject, text in args.verified
    ]
    packet = build_packet(
        args.task,
        inputs=[Path(path) for path in args.input],
        artefacts=[Path(path) for path in args.artefact],
        completed_steps=args.step,
        unknown_effects=args.unknown,
        remaining_conditions=args.condition,
        claims=claims,
    )
    text = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return 0


def _verify(args: argparse.Namespace) -> int:
    """Print the packet checked against the world now; 0 consistent, 1 not."""
    report = verify_packet(load_packet(args.packet))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["consistent"] else 1


def _command(args: argparse.Namespace) -> int:
    """Run ``build`` or ``verify``; map refusals to exit status 2."""
    try:
        return int(args.run(args))
    except (OSError, WorkStateError) as exc:
        print(f"work-state: {exc}", file=sys.stderr)
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``work-state`` with ``build`` and ``verify``.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "work-state", help="Hand over verifiable work state for a task; never authority."
    )
    nested = group.add_subparsers(dest="work_state_command", required=True)

    build = nested.add_parser("build", help="Write a packet from current inputs and repositories.")
    build.add_argument("--task", required=True, help="Task being handed over.")
    build.add_argument("--out", required=True, type=Path, help="New packet file to create.")
    build.add_argument("--input", action="append", default=[], help="File to record by SHA-256.")
    build.add_argument("--artefact", action="append", default=[], help="Repository to pin.")
    build.add_argument("--step", action="append", default=[], help="A completed step.")
    build.add_argument("--unknown", action="append", default=[], help="Unknown-outcome op id.")
    build.add_argument("--condition", action="append", default=[], help="A remaining condition.")
    build.add_argument("--agent-claim", action="append", default=[], help="An agent's claim.")
    build.add_argument("--tool-result", action="append", default=[], help="A tool's report.")
    build.add_argument(
        "--verified",
        nargs=2,
        action="append",
        default=[],
        metavar=("SUBJECT", "TEXT"),
        help="A claim resting on an input or artefact the receiver re-checks.",
    )
    build.set_defaults(func=_command, run=_build)

    verify = nested.add_parser("verify", help="Check a packet against the world now.")
    verify.add_argument("packet", type=Path, help="Packet file to verify.")
    verify.set_defaults(func=_command, run=_verify)
