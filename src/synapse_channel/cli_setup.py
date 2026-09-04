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
from collections.abc import Callable, Coroutine
from typing import Any, cast
from urllib.parse import urlsplit

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.setup_contract import canonical_json, setup_error_document
from synapse_channel.setup_inspector import inspect_setup
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


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the read-only ``setup`` command family."""
    setup = subparsers.add_parser(
        "setup",
        help="Emit a versioned setup specification or inspect this host without changing it.",
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
