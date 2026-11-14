# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ops run and ops inquire commands
"""``synapse ops run`` and ``synapse ops inquire``.

``run`` takes one catalogue operation along the checked path of
:mod:`synapse_channel.operations.runner`: exact target, the qualification
gate of the named profile, current authority,
qualified invocation, recorded start, effect, then verification or explicit
uncertainty. ``inquire`` reports a recorded run and can verify its effect;
it never runs anything.

Both print one JSON report. Exit 0: the component finished and, when a
verifier is named, the result is verified. Exit 1: the component failed or
the result is not verified. Exit 3: the outcome or the result is unknown.
Exit 2: refused, nothing was invoked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from synapse_channel.cli_ops_qualification import json_file, trusted_keys
from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.qualification_bundle import PROFILES
from synapse_channel.operations.runner import (
    OperationRunRefused,
    OperationTarget,
    RunPlan,
    RunProfile,
    RunReport,
    Verifier,
    execute,
    inquire,
    plan,
)
from synapse_channel.operations.runner_journal import RunJournalError
from synapse_channel.operations.spec import OperationSpecError
from synapse_channel.result_verification import ResultVerification, verify_file_change


def default_journal() -> Path:
    """Return the run journal directory under the Synapse data directory."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "operations" / "runs"


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _verifier(args: argparse.Namespace) -> Verifier | None:
    """Return the requested file verifier, refusing a malformed request before any run."""
    if (args.verify_file is None) != (args.verify_sha256 is None):
        raise OperationRunRefused("--verify-file and --verify-sha256 go together")
    if args.verify_file is None:
        return None
    path, digest = args.verify_file, args.verify_sha256
    if _SHA256.fullmatch(digest) is None:
        raise OperationRunRefused("--verify-sha256 is 64 lower-case hex digits")

    def verify(operation_id: str, state: str) -> ResultVerification:
        """Check the requested file for this run and its process state."""
        return verify_file_change(path, digest, operation_id=operation_id, communication=state)

    return verify


def _status(report: RunReport) -> int:
    """Map a report to the exit status: 0 done, 1 failed or not verified, 3 unknown."""
    if (
        report.state == "unknown"
        or report.verification_error
        or report.output_error
        or (report.verification is not None and report.verification.result == "not_checked")
    ):
        return 3
    if report.state != "confirmed_done" or (
        report.verification is not None and not report.verification.verified
    ):
        return 1
    return 0


def _print(report: RunReport) -> int:
    """Print the report of a run outside the hub as JSON and return its exit status."""
    print(json.dumps({**report.to_json(), "hub_governed": False}, sort_keys=True))
    return _status(report)


def _cmd_run(args: argparse.Namespace) -> int:
    """Plan and execute one operation."""
    from synapse_channel.cli_ops import default_catalogue_path, load_catalogue

    try:
        verifier = _verifier(args)
        catalogue = load_catalogue(
            Path(args.catalogue) if args.catalogue else default_catalogue_path()
        )
        if not isinstance(catalogue, Catalogue):
            raise OperationRunRefused("a catalogue of the earlier schema is never run")
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise OperationRunRefused("--arguments is a JSON object")
        target = OperationTarget(
            args.operation,
            args.host,
            args.vendor,
            args.component,
            args.component_version,
            args.os,
            args.transport,
            args.variant,
        )
        profile = RunProfile(
            args.profile,
            json_file(args.bundle) if args.bundle else None,
            trusted_keys(args.trust),
            tuple(args.require_environment),
        )
        _check_hub_options(args)
        prepared = plan(catalogue, target, arguments, profile=profile)
        return _hub_path(args, prepared, verifier) if args.hub else _local(args, prepared, verifier)
    except (OSError, ValueError, OperationSpecError) as exc:
        print(f"synapse ops run: refused: {exc}", file=sys.stderr)
        return 2


def _local(args: argparse.Namespace, prepared: RunPlan, verifier: Verifier | None) -> int:
    """Execute the planned run under the local grants alone, outside the hub."""
    try:
        report = execute(
            prepared,
            operation_id=args.operation_id,
            subject=args.subject,
            grants_path=args.grants,
            journal=args.journal or default_journal(),
            timeout=args.timeout,
            verifier=verifier,
        )
    except (OperationRunRefused, RunJournalError) as exc:
        # Only refusals reach here: execute reports every failure after the start.
        print(f"synapse ops run: refused: {exc}", file=sys.stderr)
        return 2
    return _print(report)


def _check_hub_options(args: argparse.Namespace) -> None:
    """Refuse hub options without ``--hub``, and an incomplete hub request."""
    hub_only = ("task", "epoch", "intent_deadline", "intent_sha256", "idem_key", "name", "token")
    if not args.hub:
        named = [name for name in hub_only if getattr(args, name) is not None] + (
            ["declare"] if args.declare else []
        )
        if named:
            raise OperationRunRefused(f"--{named[0].replace('_', '-')} needs --hub")
        return
    if args.task is None or args.epoch is None:
        raise OperationRunRefused("the hub path needs --task and --epoch")
    if args.declare == (args.intent_sha256 is not None):
        raise OperationRunRefused("the hub path takes exactly one of --declare and --intent-sha256")


def _hub_path(args: argparse.Namespace, prepared: RunPlan, verifier: Verifier | None) -> int:
    """Declare the planned run on the hub, or run it inside one approved attempt."""
    from synapse_channel.cli_ops_hub import declare, run_attempt

    if args.declare:
        return declare(args, prepared)
    return run_attempt(
        args, prepared, verifier, journal=args.journal or default_journal(), status=_status
    )


def _cmd_inquire(args: argparse.Namespace) -> int:
    """Report one recorded run; never run it."""
    try:
        report = inquire(
            args.journal or default_journal(), args.operation_id, verifier=_verifier(args)
        )
    except (OSError, ValueError, RunJournalError) as exc:
        print(f"synapse ops inquire: {exc}", file=sys.stderr)
        return 2
    return _print(report)


def _verification_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the verifier and journal options shared by ``run`` and ``inquire``."""
    parser.add_argument("--verify-file", type=Path, help="File the operation must produce.")
    parser.add_argument("--verify-sha256", help="Requested content SHA-256 of that file.")
    parser.add_argument("--journal", type=Path, help="Run journal directory.")


def _hub_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the options of the opt-in hub path; without ``--hub`` none of them is allowed."""
    hub = parser.add_argument_group("hub path (opt-in)")
    hub.add_argument("--hub", default=None, help="Hub URI; run through its governed operations.")
    hub.add_argument("--task", default=None, help="The claimed task the operation is declared on.")
    hub.add_argument("--epoch", type=int, default=None, help="The claim's epoch.")
    hub.add_argument("--name", default=None, help="Identity on the hub (default: --subject).")
    hub.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    hub.add_argument(
        "--intent-deadline", type=int, default=None, help="The intent's deadline, UNIX seconds."
    )
    hub.add_argument("--idem-key", default=None, help="Key to ask again for a lost attempt answer.")
    step = hub.add_mutually_exclusive_group()
    step.add_argument("--declare", action="store_true", help="Declare the run; run nothing.")
    step.add_argument("--intent-sha256", default=None, help="Run inside an approved attempt.")


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``run`` and ``inquire`` below ``synapse ops``.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        The ``synapse ops`` subcommand group.
    """
    run = commands.add_parser("run", help="Run one admitted operation along the checked path.")
    run.add_argument("operation", help="Dotted operation name.")
    for name in ("host", "vendor", "component", "component-version", "os"):
        run.add_argument(f"--{name}", required=True, help=f"Exact {name} of the row.")
    run.add_argument("--transport", default="cli", help="Transport of the row (cli).")
    run.add_argument("--variant", default="default", help="Variant of the row.")
    run.add_argument("--arguments", default="{}", help="Operation arguments as a JSON object.")
    run.add_argument("--operation-id", required=True, help="Stable id; never run twice.")
    run.add_argument("--subject", required=True, help="Seat whose grants authorise the run.")
    run.add_argument("--grants", required=True, type=Path, help="Operator grants file.")
    run.add_argument("--profile", required=True, choices=sorted(PROFILES))
    run.add_argument("--bundle", type=Path, help="Signed qualification bundle (production).")
    run.add_argument("--trust", type=Path, help="Trusted qualifier keys (production).")
    run.add_argument(
        "--require-environment",
        action="append",
        default=[],
        help="Environment component the profile names; repeat for each.",
    )
    run.add_argument("--timeout", type=float, default=60.0, help="Deadline in seconds.")
    run.add_argument("--catalogue", default=None, help="Catalogue file to read.")
    _verification_arguments(run)
    _hub_arguments(run)
    run.set_defaults(func=_cmd_run)

    ask = commands.add_parser("inquire", help="Report a recorded run; never run it.")
    ask.add_argument("--operation-id", required=True, help="Id of the recorded run.")
    _verification_arguments(ask)
    ask.set_defaults(func=_cmd_inquire)
