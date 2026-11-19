# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verify-result command
"""``synapse verify-result``: verify a requested result, not the exchange.

Each subcommand prints one ``synapse.result-verification.v1`` JSON record and
exits 0 when the result is verified, 1 when it is not, 3 when the verifier
could not decide, and 2 on unusable input. ``--outcome`` reads the boundary
state and operation identity from an execution-outcome report, so a worker
that exited 0 next to a wrong or absent effect is shown as exactly that.
``git`` with ``--attempt-id``, ``--intent-sha256`` and ``--base`` also reads
that attempt's marker beside the branch. ``component`` runs a catalogue read row
that observes a component's own state, under the verifier's own grants, and
compares the digest of its projection (:mod:`synapse_channel.operations.component_state`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 # fixed argv only, never a shell
import sys
import uuid
from pathlib import Path

from synapse_channel.result_verification import (
    ResultVerification,
    read_outcome,
    verify_file_change,
    verify_git_publication,
)
from synapse_channel.result_verification_attempt import AttemptCheck
from synapse_channel.result_verification_effects import (
    schema_digest,
    verify_deployment,
    verify_migration,
)

_EXIT = {"verified": 0, "not_verified": 1, "not_checked": 3}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BASE_GENERATION = re.compile(r"0|[1-9][0-9]{0,19}")


def _identity(args: argparse.Namespace) -> tuple[str, str]:
    """Return the operation identity and boundary state to report."""
    if args.outcome is None:
        if not args.operation_id:
            raise ValueError("give --operation-id or --outcome")
        return args.operation_id, "not_supplied"
    operation_id, state = read_outcome(args.outcome)
    if args.operation_id and args.operation_id != operation_id:
        raise ValueError("--operation-id differs from the outcome report")
    return operation_id, state


def _report(record: ResultVerification) -> int:
    print(json.dumps(record.to_json(), sort_keys=True))
    return _EXIT[record.result]


def _file(args: argparse.Namespace) -> int:
    operation_id, communication = _identity(args)
    return _report(
        verify_file_change(
            args.path,
            args.sha256,
            operation_id=operation_id,
            communication=communication,
            tests=args.tests,
            cwd=args.cwd,
            timeout=args.timeout,
        )
    )


def _attempt(args: argparse.Namespace) -> AttemptCheck | None:
    """Return the attempt the options name, ``None`` for none, or refuse a partial set."""
    given = (args.attempt_id, args.intent_sha256, args.base)
    if not any(given):
        return None
    if not all(given):
        raise ValueError("give --attempt-id, --intent-sha256 and --base together, or none of them")
    return AttemptCheck(args.attempt_id, args.intent_sha256, args.base)


def _git(args: argparse.Namespace) -> int:
    attempt = _attempt(args)
    operation_id, communication = _identity(args)
    return _report(
        verify_git_publication(
            args.repo,
            args.remote,
            args.branch,
            args.commit,
            operation_id=operation_id,
            communication=communication,
            timeout=args.timeout,
            attempt=attempt,
        )
    )


def _parse_base_generation(raw: str | None) -> int | None:
    """Parse canonical non-negative decimal ``--base-generation`` digits."""
    if raw is None:
        return None
    if _BASE_GENERATION.fullmatch(raw) is None:
        raise ValueError("--base-generation must be canonical non-negative decimal digits")
    return int(raw)


def _deployment(args: argparse.Namespace) -> int:
    base_generation = _parse_base_generation(args.base_generation)
    operation_id, communication = _identity(args)
    return _report(
        verify_deployment(
            args.report,
            args.revision,
            operation_id=operation_id,
            communication=communication,
            chain=args.chain,
            start_record=args.start_record,
            base_generation=base_generation,
            base_artefact_sha256=args.base_artefact_sha256,
            artefact_sha256=args.artefact_sha256,
            attempt_id=args.attempt_id,
        )
    )


def _migration(args: argparse.Namespace) -> int:
    if args.print_schema_sha256:
        print(schema_digest(args.database))
        return 0
    if not args.migration or not args.schema_sha256:
        raise ValueError("give --migration and --schema-sha256, or --print-schema-sha256")
    operation_id, communication = _identity(args)
    return _report(
        verify_migration(
            args.database,
            args.migration,
            args.schema_sha256,
            operation_id=operation_id,
            communication=communication,
            migrations_table=args.migrations_table,
            migration_column=args.migration_column,
        )
    )


def _component(args: argparse.Namespace) -> int:
    """Run the observing read row and compare the digest of its projection."""
    from synapse_channel.cli_ops import default_catalogue_path, load_catalogue
    from synapse_channel.cli_ops_qualification import json_file, trusted_keys
    from synapse_channel.cli_ops_run import default_journal
    from synapse_channel.operations.catalogue import Catalogue
    from synapse_channel.operations.component_state import plan_read, verify_component
    from synapse_channel.operations.runner import OperationTarget, RunProfile

    if _HEX64.fullmatch(args.expect_sha256) is None:
        raise ValueError("--expect-sha256 is 64 lower-case hex digits")
    operation_id, communication = _identity(args)
    catalogue = load_catalogue(Path(args.catalogue) if args.catalogue else default_catalogue_path())
    if not isinstance(catalogue, Catalogue):
        raise ValueError("a catalogue of the earlier schema observes nothing")
    profile = RunProfile(
        args.profile,
        json_file(args.bundle) if args.bundle else None,
        trusted_keys(args.trust),
        tuple(args.require_environment),
    )
    target = OperationTarget(
        args.observed_by,
        args.host,
        args.vendor,
        args.component,
        args.component_version,
        args.os,
        args.transport,
        args.variant,
    )
    return _report(
        verify_component(
            plan_read(catalogue, target, profile),
            args.expect_sha256,
            operation_id=operation_id,
            communication=communication,
            read_id=f"{operation_id[:80]}.verify-{uuid.uuid4().hex[:16]}",
            subject=args.subject,
            grants_path=args.grants,
            journal=args.journal or default_journal(),
            timeout=args.timeout,
        )
    )


def _command(args: argparse.Namespace) -> int:
    """Run the selected verifier and map refusals to exit status 2."""
    try:
        return int(args.verify(args))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"verify-result: {exc}", file=sys.stderr)
        return 2


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", help="Operation being verified.")
    parser.add_argument(
        "--outcome", type=Path, help="Execution-outcome report giving identity and boundary state."
    )
    parser.set_defaults(func=_command)


def _add_git_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``verify-result git``, with the optional attempt whose marker it also reads."""
    git_parser = nested.add_parser("git", help="Verify that a commit is on a remote branch.")
    git_parser.add_argument("--repo", type=Path, default=Path("."), help="Local repository.")
    git_parser.add_argument("--remote", required=True, help="Remote name or URL.")
    git_parser.add_argument("--branch", required=True, help="Branch on the remote.")
    git_parser.add_argument(
        "--commit",
        required=True,
        help="Full commit identity: 40 hex, or 64 in a SHA-256 repository with --attempt-id.",
    )
    git_parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per git call.")
    git_parser.add_argument(
        "--attempt-id", help="Also read this attempt's marker beside the branch."
    )
    git_parser.add_argument("--intent-sha256", help="The digest of the operation's intent.")
    git_parser.add_argument("--base", help="The commit the attempt's effect starts from.")
    git_parser.set_defaults(verify=_git)
    _common(git_parser)


def _add_component_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``verify-result component``: a read row observes a component's own state."""
    from synapse_channel.operations.qualification_bundle import PROFILES

    parser = nested.add_parser(
        "component",
        help="Run the read row that observes a component's own state and compare its digest.",
    )
    parser.add_argument("--observed-by", required=True, help="The observing read operation.")
    for name in ("host", "vendor", "component", "component-version", "os"):
        parser.add_argument(f"--{name}", required=True, help=f"Exact {name} of the row.")
    parser.add_argument("--transport", default="cli", help="Transport of the row (cli).")
    parser.add_argument("--variant", default="default", help="Variant of the row.")
    parser.add_argument(
        "--expect-sha256", required=True, help="Declared digest of the projection after the change."
    )
    parser.add_argument("--subject", required=True, help="Seat whose grants authorise the read.")
    parser.add_argument("--grants", required=True, type=Path, help="Operator grants file.")
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--bundle", type=Path, help="Signed qualification bundle (production).")
    parser.add_argument("--trust", type=Path, help="Trusted qualifier keys (production).")
    parser.add_argument(
        "--require-environment",
        action="append",
        default=[],
        help="Environment component the profile names; repeat for each.",
    )
    parser.add_argument("--catalogue", default=None, help="Catalogue file to read.")
    parser.add_argument("--journal", type=Path, help="Run journal directory.")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Deadline of the read, seconds."
    )
    parser.set_defaults(verify=_component)
    _common(parser)


def _add_deployment_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``verify-result deployment`` for report-based and chained targets."""
    deployment_parser = nested.add_parser(
        "deployment",
        help="Check a health report or a generation chain plus service start record.",
    )
    deployment_parser.add_argument(
        "--revision", required=True, help="Revision the deployment must name."
    )
    deployment_parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Legacy JSON health report (mutually exclusive with --chain).",
    )
    deployment_parser.add_argument(
        "--chain",
        type=Path,
        default=None,
        help="Owner-only synapse-generation.v1 directory (chained mode).",
    )
    deployment_parser.add_argument(
        "--start-record",
        type=Path,
        default=None,
        help="Deployed service's synapse-service-start.v1 file (chained mode).",
    )
    deployment_parser.add_argument(
        "--base-generation",
        default=None,
        help="Declared pre-state generation >= 0 (chained mode).",
    )
    deployment_parser.add_argument(
        "--base-artefact-sha256",
        default=None,
        help="Declared 64-hex pre-state artefact digest (64 zeros at base 0).",
    )
    deployment_parser.add_argument(
        "--artefact-sha256",
        default=None,
        help="Declared 64-hex target artefact digest at base + 1.",
    )
    deployment_parser.add_argument(
        "--attempt-id",
        default=None,
        help="Attempt expected to have published base + 1 (chained mode).",
    )
    deployment_parser.set_defaults(verify=_deployment)
    _common(deployment_parser)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``verify-result`` with file, git, deployment, migration and component.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "verify-result",
        help=(
            "Verify a requested result (file, git, deployment, migration, component); "
            "never redoes work."
        ),
    )
    nested = group.add_subparsers(dest="verify_result_command", required=True)

    file_parser = nested.add_parser("file", help="Verify a file's exact content, then its tests.")
    file_parser.add_argument("--path", required=True, type=Path, help="File the work produced.")
    file_parser.add_argument("--sha256", required=True, help="Requested content SHA-256.")
    file_parser.add_argument("--cwd", type=Path, help="Directory for the tests.")
    file_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds for the tests.")
    file_parser.add_argument("tests", nargs="*", help="Test argv after --, run if content matches.")
    file_parser.set_defaults(verify=_file)
    _common(file_parser)

    _add_git_parser(nested)
    _add_deployment_parser(nested)

    migration_parser = nested.add_parser(
        "migration", help="Verify a migration identity and the resulting SQLite schema."
    )
    migration_parser.add_argument("--database", required=True, type=Path, help="SQLite database.")
    migration_parser.add_argument("--migration", help="Applied migration identity.")
    migration_parser.add_argument("--schema-sha256", help="SHA-256 of the requested schema text.")
    migration_parser.add_argument(
        "--print-schema-sha256",
        action="store_true",
        help="Print the database schema digest and exit 0. No verdict.",
    )
    migration_parser.add_argument(
        "--migrations-table", default="schema_migrations", help="Table that records migrations."
    )
    migration_parser.add_argument(
        "--migration-column", default="version", help="Column that stores the migration identity."
    )
    migration_parser.set_defaults(verify=_migration)
    _common(migration_parser)

    _add_component_parser(nested)
