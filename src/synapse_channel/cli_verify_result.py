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
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 # fixed argv only, never a shell
import sys
from pathlib import Path

from synapse_channel.result_verification import (
    ResultVerification,
    read_outcome,
    verify_file_change,
    verify_git_publication,
)
from synapse_channel.result_verification_effects import (
    schema_digest,
    verify_deployment,
    verify_migration,
)

_EXIT = {"verified": 0, "not_verified": 1, "not_checked": 3}


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


def _git(args: argparse.Namespace) -> int:
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
        )
    )


def _deployment(args: argparse.Namespace) -> int:
    operation_id, communication = _identity(args)
    return _report(
        verify_deployment(
            args.report,
            args.revision,
            operation_id=operation_id,
            communication=communication,
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


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``verify-result`` with file, git, deployment and migration.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "verify-result",
        help=("Verify a requested result (file, git, deployment, migration); never redoes work."),
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

    git_parser = nested.add_parser("git", help="Verify that a commit is on a remote branch.")
    git_parser.add_argument("--repo", type=Path, default=Path("."), help="Local repository.")
    git_parser.add_argument("--remote", required=True, help="Remote name or URL.")
    git_parser.add_argument("--branch", required=True, help="Branch on the remote.")
    git_parser.add_argument("--commit", required=True, help="Full 40-hex commit identity.")
    git_parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per git call.")
    git_parser.set_defaults(verify=_git)
    _common(git_parser)

    deployment_parser = nested.add_parser(
        "deployment",
        help="Check a health report's revision and health. It does not observe the service.",
    )
    deployment_parser.add_argument(
        "--revision", required=True, help="Revision the report must name."
    )
    deployment_parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help="JSON report. Independent only when the deployed service wrote it.",
    )
    deployment_parser.set_defaults(verify=_deployment)
    _common(deployment_parser)

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
