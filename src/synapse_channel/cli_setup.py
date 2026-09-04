# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — machine-readable setup specification and inspection CLI
"""CLI surface for safe, agent-consumable environment setup discovery."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.setup_authorization import (
    SetupAuthorizationError,
    build_setup_authorization,
    load_setup_authorization,
    load_setup_plan,
)
from synapse_channel.setup_contract import canonical_json, setup_error_document
from synapse_channel.setup_executor import SetupExecutionError, apply_setup
from synapse_channel.setup_inspector import inspect_setup
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import build_setup_spec, get_setup_profile


def _print_document(document: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(canonical_json(document))
        return
    kind = document["document_kind"]
    if kind == "error":
        print(f"setup error [{document['code']}]: {document['message']}", file=sys.stderr)
        return
    print(f"SYNAPSE setup {kind}: {document['profile']} v{document['profile_version']}")
    if kind == "spec":
        print(document["summary"])
        requirements = cast(list[dict[str, object]], document["requirements"])
        for requirement in requirements:
            marker = "required" if requirement["required"] else "optional"
            print(f"- {requirement['id']} ({marker}): {requirement['description']}")
        return
    if kind == "plan":
        effects = cast(list[dict[str, object]], document["effects"])
        disposition = "applicable" if document["can_apply"] else "not applicable"
        print(f"plan: {len(effects)} proposed effect(s); {disposition}")
        print(f"digest: {document['plan_digest']}")
        for effect in effects:
            print(
                f"- {effect['disposition']} {effect['id']}: "
                f"{effect['authority']} / {effect['disruption']}"
            )
        return
    if kind == "authorization":
        print("authorization: single use; pass with its exact plan to setup apply")
        print(f"digest: {document['authorization_digest']}")
        print(f"plan: {document['plan_digest']}")
        print(f"expires: {document['expires_at']}")
        return
    if kind == "application_receipt":
        print(f"outcome: {document['outcome']}")
        print(f"receipt: {document['receipt_digest']}")
        print(f"ledger: {document['ledger_state']}")
        return
    ready = "ready" if document["ready"] else "not ready"
    print(f"result: {ready} (read-only)")
    checks = cast(list[dict[str, object]], document["checks"])
    for check in checks:
        print(f"- {check['status']} {check['id']}: {check['detail']}")


def _cmd_spec(args: argparse.Namespace) -> int:
    profile = get_setup_profile(args.profile)
    if profile is None:
        _print_document(
            setup_error_document(command="spec", profile=args.profile, code="unknown_profile"),
            as_json=args.json,
        )
        return 2
    _print_document(build_setup_spec(profile), as_json=args.json)
    return 0


def _valid_inspection_uri(value: str) -> bool:
    """Accept a WebSocket endpoint only when it cannot carry inline secrets."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"ws", "wss"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 0 < port <= 65535)
    )


def _cmd_inspect(
    args: argparse.Namespace,
    *,
    async_runner: Callable[
        [Coroutine[Any, Any, dict[str, object]]], dict[str, object]
    ] = asyncio.run,
) -> int:
    profile = get_setup_profile(args.profile)
    if profile is None:
        _print_document(
            setup_error_document(command="inspect", profile=args.profile, code="unknown_profile"),
            as_json=args.json,
        )
        return 2
    if not _valid_inspection_uri(args.uri):
        _print_document(
            setup_error_document(command="inspect", profile=args.profile, code="invalid_uri"),
            as_json=args.json,
        )
        return 2
    try:
        document = async_runner(
            inspect_setup(
                profile,
                uri=args.uri,
                project=args.project,
                agent_id=args.id,
            )
        )
    except Exception:  # noqa: BLE001 - keep the CLI error contract bounded
        _print_document(
            setup_error_document(command="inspect", profile=args.profile, code="inspection_failed"),
            as_json=args.json,
        )
        return 2
    _print_document(document, as_json=args.json)
    return 0 if document["ready"] is True else 1


def _cmd_plan(
    args: argparse.Namespace,
    *,
    async_runner: Callable[
        [Coroutine[Any, Any, dict[str, object]]], dict[str, object]
    ] = asyncio.run,
) -> int:
    profile = get_setup_profile(args.profile)
    if profile is None:
        _print_document(
            setup_error_document(command="plan", profile=args.profile, code="unknown_profile"),
            as_json=args.json,
        )
        return 2
    if not _valid_inspection_uri(args.uri):
        _print_document(
            setup_error_document(command="plan", profile=args.profile, code="invalid_uri"),
            as_json=args.json,
        )
        return 2
    try:
        inspection = async_runner(
            inspect_setup(
                profile,
                uri=args.uri,
                project=args.project,
                agent_id=args.id,
            )
        )
        document = build_setup_plan(profile, inspection)
    except Exception:  # noqa: BLE001 - keep the CLI error contract bounded
        _print_document(
            setup_error_document(command="plan", profile=args.profile, code="planning_failed"),
            as_json=args.json,
        )
        return 2
    _print_document(document, as_json=args.json)
    return 0


def _cmd_authorize(args: argparse.Namespace) -> int:
    profile = "unknown"
    try:
        plan = load_setup_plan(args.plan)
        plan_profile = plan.get("profile")
        if isinstance(plan_profile, str):
            profile = plan_profile
        document = build_setup_authorization(
            plan,
            confirm_digest=args.confirm_digest,
            nonce=args.nonce,
            expires_in=args.expires_in,
            restart_pid=args.authorize_restart_pid,
        )
    except SetupAuthorizationError as exc:
        _print_document(
            setup_error_document(command="authorize", profile=profile, code=exc.code),
            as_json=args.json,
        )
        return 2
    except Exception:  # noqa: BLE001 - keep the CLI error contract bounded
        _print_document(
            setup_error_document(
                command="authorize",
                profile=profile,
                code="authorization_failed",
            ),
            as_json=args.json,
        )
        return 2
    _print_document(document, as_json=args.json)
    return 0


def _cmd_apply(
    args: argparse.Namespace,
    *,
    async_runner: Callable[
        [Coroutine[Any, Any, dict[str, object]]], dict[str, object]
    ] = asyncio.run,
) -> int:
    profile = "unknown"
    try:
        plan = load_setup_plan(args.plan)
        plan_profile = plan.get("profile")
        if isinstance(plan_profile, str):
            profile = plan_profile
        authorization = load_setup_authorization(
            args.authorization,
            plan=plan,
            now=int(time.time()),
        )
        document = async_runner(
            apply_setup(
                plan,
                authorization,
                confirm_digest=args.confirm_digest,
                protected_pids=tuple(args.protect_pid),
                receipt_path=args.receipt,
            )
        )
    except SetupExecutionError as exc:
        if exc.receipt is not None:
            _print_document(exc.receipt, as_json=args.json)
        else:
            _print_document(
                setup_error_document(command="apply", profile=profile, code=exc.code),
                as_json=args.json,
            )
        return 2
    except SetupAuthorizationError as exc:
        _print_document(
            setup_error_document(command="apply", profile=profile, code=exc.code),
            as_json=args.json,
        )
        return 2
    except Exception:  # noqa: BLE001 - keep the CLI error contract bounded
        _print_document(
            setup_error_document(
                command="apply",
                profile=profile,
                code="application_effect_failed",
            ),
            as_json=args.json,
        )
        return 2
    _print_document(document, as_json=args.json)
    if document["outcome"] == "applied":
        return 0
    return 1 if document["outcome"] == "recovered" else 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register machine-readable setup inspection and authorized application."""
    setup = subparsers.add_parser(
        "setup",
        help="Inspect, plan, authorize, and apply package-owned setup effects.",
    )
    actions = setup.add_subparsers(dest="setup_command", required=True)

    spec = actions.add_parser("spec", help="Print the package-owned setup profile contract.")
    spec.add_argument("--profile", required=True)
    spec.add_argument("--json", action="store_true", help="Emit one canonical JSON document.")
    spec.set_defaults(func=_cmd_spec)

    inspect = actions.add_parser(
        "inspect", help="Observe host, identity, hub, and waiter evidence."
    )
    inspect.add_argument("--profile", required=True)
    inspect.add_argument("--uri", default=default_hub_uri())
    inspect.add_argument("--project", default=None)
    inspect.add_argument("--id", default=None)
    inspect.add_argument("--json", action="store_true", help="Emit one canonical JSON document.")
    inspect.set_defaults(func=_cmd_inspect)

    plan = actions.add_parser(
        "plan", help="Derive a digest-bound future effect plan without applying it."
    )
    plan.add_argument("--profile", required=True)
    plan.add_argument("--uri", default=default_hub_uri())
    plan.add_argument("--project", default=None)
    plan.add_argument("--id", default=None)
    plan.add_argument("--json", action="store_true", help="Emit one canonical JSON document.")
    plan.set_defaults(func=_cmd_plan)

    authorize = actions.add_parser(
        "authorize",
        help="Emit an expiring authorization envelope without applying its plan.",
    )
    authorize.add_argument("--plan", required=True, metavar="FILE")
    authorize.add_argument("--confirm-digest", required=True, metavar="SHA256")
    authorize.add_argument("--nonce", required=True, metavar="TOKEN")
    authorize.add_argument("--expires-in", type=int, default=300, metavar="SECONDS")
    authorize.add_argument("--authorize-restart-pid", type=int, metavar="PID")
    authorize.add_argument("--json", action="store_true", help="Emit one canonical JSON document.")
    authorize.set_defaults(func=_cmd_authorize)

    apply = actions.add_parser(
        "apply",
        help="Consume one exact authorization and apply its allow-listed Linux service effects.",
    )
    apply.add_argument("--plan", required=True, type=Path, metavar="FILE")
    apply.add_argument("--authorization", required=True, type=Path, metavar="FILE")
    apply.add_argument("--confirm-digest", required=True, metavar="SHA256")
    apply.add_argument("--receipt", type=Path, metavar="FILE")
    apply.add_argument("--protect-pid", type=int, action="append", default=[], metavar="PID")
    apply.add_argument("--json", action="store_true", help="Emit one canonical JSON document.")
    apply.set_defaults(func=_cmd_apply)
