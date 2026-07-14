# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse fleet-init`: empty machine to working fleet in one command
"""``synapse fleet-init`` — empty machine to working fleet in one command.

The pieces of a first fleet already exist as separate verbs: ``doctor`` (``--fix``
repairs the default local hub and waiter), ``new coding-fleet`` (the workspace
scaffold), ``participant list`` (which provider CLIs this machine can seat), and the
packaged no-collision demo. A newcomer had to discover that sequence; this command
*is* the sequence. It runs the health check, scaffolds a persistent workspace,
probes the model seats, runs the demo smoke, and prints the concrete next-steps
plan with the workspace's own project name filled in.

It deliberately adds no machinery: no new dependency, no new daemon — everything it
starts is exactly what the bundled commands start, and every stage's outcome is
reported honestly (a failing doctor is a note and a remedy, not a hidden retry).
The seats stage is a report, not a gate: an unavailable declared seat is warned
about and kept in the plan, because installing the provider CLI is the operator's
next step, not this command's business.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from synapse_channel.cli_participants import (
    DEFAULT_ASK_TIMEOUT,
    PROVIDERS,
    build_participant,
    refusal_for,
)
from synapse_channel.coding_fleet import main as run_coding_fleet_demo
from synapse_channel.coding_fleet_template import create_coding_fleet
from synapse_channel.terminal_text import shell_command_arg, shell_long_option, terminal_text

DEFAULT_WORKSPACE = "synapse-fleet"
"""Workspace directory created when no path is given — persistent, not temporary."""

DoctorStage = Callable[[bool], int]
SeatProbe = Callable[[str], tuple[bool, str]]
WorkspaceCreator = Callable[..., list[str]]
DemoRunner = Callable[[], int]


def run_doctor_stage(fix: bool) -> int:
    """Run the real ``synapse doctor`` (optionally ``--fix``) and return its exit code.

    The doctor's own parser builds the namespace, so every doctor default — present
    and future — is honoured without this module mirroring them.
    """
    from synapse_channel.cli_doctor import add_parsers as doctor_add_parsers

    parser = argparse.ArgumentParser(prog="synapse")
    doctor_add_parsers(parser.add_subparsers(dest="command"))
    namespace = parser.parse_args(["doctor", *(["--fix"] if fix else [])])
    return int(namespace.func(namespace))


def probe_seat(provider: str) -> tuple[bool, str]:
    """Probe one provider's readiness without taking a turn; return (available, detail)."""
    participant = build_participant(
        provider,
        identity=f"participant/{provider}",
        model="",
        timeout=DEFAULT_ASK_TIMEOUT,
        probe=True,
    )
    health = participant.health()
    return health.available, health.detail


def _cmd_fleet_init(
    args: argparse.Namespace,
    *,
    doctor_stage: DoctorStage = run_doctor_stage,
    creator: WorkspaceCreator = create_coding_fleet,
    seat_probe: SeatProbe = probe_seat,
    demo_runner: DemoRunner = run_coding_fleet_demo,
) -> int:
    """Run the four onboarding stages and print the next-steps plan.

    Exit code: ``2`` for a refused configuration (unknown ``--seat``, unsafe
    workspace), the demo's own code when the smoke fails, ``0`` otherwise — a
    failing doctor stage is reported with its remedy but does not abort the
    onboarding it exists to enable.
    """
    unknown = [seat for seat in args.seat if seat not in PROVIDERS]
    if unknown:
        known = ", ".join(sorted(PROVIDERS))
        print(
            f"synapse fleet-init: unknown --seat {', '.join(sorted(unknown))}; "
            f"known providers: {known}",
            file=sys.stderr,
        )
        return 2

    print("== 1/4 doctor ==")
    doctor_code = doctor_stage(args.fix)
    if doctor_code != 0:
        print(
            "doctor reported findings (see above); `synapse doctor --fix` repairs "
            "the default local hub and waiter."
        )

    print("== 2/4 workspace ==")
    workspace = Path(args.path if args.path is not None else DEFAULT_WORKSPACE)
    try:
        for line in creator(workspace, force=args.force):
            print(terminal_text(line))
    except FileExistsError as exc:
        print(f"synapse fleet-init: {terminal_text(exc)}", file=sys.stderr)
        return 2

    print("== 3/4 model seats ==")
    seats = _report_seats(args.seat, seat_probe)

    smoke_code = 0
    print("== 4/4 demo smoke ==")
    if args.no_smoke:
        print("skipped (--no-smoke)")
    else:
        smoke_code = demo_runner()

    _print_plan(workspace, seats, doctor_ok=doctor_code == 0)
    return smoke_code


def _report_seats(declared: list[str], seat_probe: SeatProbe) -> list[str]:
    """Print each provider's probed readiness; return the seats the plan should list.

    With ``--seat`` the declared providers are the plan (an unavailable one is
    warned about and kept); with none, the plan lists whatever probed available.
    """
    available: list[str] = []
    for provider in sorted(PROVIDERS):
        is_available, detail = seat_probe(provider)
        state = "available" if is_available else "unavailable"
        note = " [participant turns disabled]" if refusal_for(provider) is not None else ""
        print(f"  {terminal_text(provider)} {state}: {terminal_text(detail)}{note}")
        if is_available:
            available.append(provider)
    if not declared:
        return available
    for seat in declared:
        if seat not in available:
            print(
                f"  warning: declared seat {terminal_text(seat)!r} "
                "is not available on this machine yet"
            )
    return list(declared)


def _print_plan(workspace: Path, seats: list[str], *, doctor_ok: bool) -> None:
    """Print the concrete next-steps plan with the workspace's project name filled in."""
    project = workspace.name
    print("== next steps ==")
    step = 1
    if not doctor_ok:
        print(f"  {step}. repair the local hub and waiter:  synapse doctor --fix")
        step += 1
    print(
        f"  {step}. try the scaffolded fleet:          "
        f"cd -- {shell_command_arg(workspace)} && python run_demo.py"
    )
    step += 1
    print(
        f"  {step}. arm your wake listener:            "
        f"synapse arm {shell_long_option('--name', project)} "
        f"{shell_long_option('--for', f'{project},{project}/*')}"
    )
    step += 1
    if seats:
        print(f"  {step}. seat your agents (each in its own terminal):")
        for seat in seats:
            print(
                f"       synapse worker-session {shell_long_option('--identity', project)} -- "
                f"{shell_command_arg(seat)}"
            )
        step += 1
    print(f"  {step}. claim-aware git hooks in your repo: synapse git-init")
    step += 1
    print(f"  {step}. watch the fleet:                   synapse dashboard")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``synapse fleet-init`` command."""
    parser = subparsers.add_parser(
        "fleet-init",
        help="Empty machine to working fleet: doctor, workspace scaffold, seat probe, "
        "demo smoke, and a printed next-steps plan — one command.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"Workspace directory to create (default: ./{DEFAULT_WORKSPACE}).",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Let the doctor stage repair the default local hub and waiter "
        "(same as `synapse doctor --fix`).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh generated files in an existing workspace without deleting unrelated files.",
    )
    parser.add_argument(
        "--seat",
        action="append",
        default=[],
        metavar="PROVIDER",
        help="Declare a provider CLI to seat (repeatable); the plan lists declared "
        "seats even when not yet installed. Default: whatever probes available.",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip the packaged no-collision demo run.",
    )
    parser.set_defaults(func=_cmd_fleet_init)
