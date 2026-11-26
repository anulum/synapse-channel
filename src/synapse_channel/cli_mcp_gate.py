# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the ``synapse mcp-gate`` command
"""``synapse mcp-gate`` — a stable-bearer gate in front of the authenticated HTTP MCP listener.

``serve`` runs the gate; ``new-bearer`` creates a seat's stable bearer and
stores only its digest. The secret goes to a new owner-only ``--secret-out``
file, or to stdout once with ``--print-secret``. See ``docs/grok-remote.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_token_set import _write_secret_out
from synapse_channel.mcp.gate import GateState
from synapse_channel.mcp.gate_config import (
    GateConfigError,
    check_bearers,
    check_seat_grants,
    load_gate_config,
    new_bearer,
)
from synapse_channel.mcp.gate_server import serve_gate
from synapse_channel.terminal_text import terminal_text

Runner = Callable[[Coroutine[Any, Any, None]], None]
_NO_DESTINATION = (
    "synapse mcp-gate new-bearer: name where the generated secret goes: "
    "--secret-out FILE writes it to a new owner-only file; "
    "--print-secret prints it once on stdout"
)


def _cmd_serve(args: argparse.Namespace, *, runner: Runner = asyncio.run) -> int:
    """Validate the configuration and the seats' grants, then serve until stopped."""
    try:
        config = load_gate_config(Path(args.config))
        check_seat_grants(config)
        check_bearers(config)
    except GateConfigError as exc:
        print(f"configuration error: {terminal_text(exc)}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        runner(serve_gate(GateState(config)))
    except KeyboardInterrupt:
        return 0
    return 0


def _cmd_new_bearer(args: argparse.Namespace) -> int:
    """Create the named seat's bearer and deliver the secret where asked.

    Exactly one of ``--secret-out`` and ``--print-secret`` is required. With
    neither, or with an existing output path, nothing is stored. A secret file
    is written before the record; if the record is not stored, that file is
    removed, so a failed rotation leaves the old bearer in place.
    """
    secret_out = Path(args.secret_out).expanduser() if args.secret_out else None
    if secret_out is None and not args.print_secret:
        print(_NO_DESTINATION, file=sys.stderr)
        return 2
    if secret_out is not None and secret_out.exists():
        print(f"synapse mcp-gate new-bearer: {secret_out} already exists", file=sys.stderr)
        return 2
    try:
        config = load_gate_config(Path(args.config))
        seats = {seat.seat: seat for seat in config.seats}
        if args.seat not in seats:
            raise GateConfigError(f"the configuration has no seat {args.seat}")
        bearer_path = seats[args.seat].bearer_file
        if secret_out is None:
            secret = new_bearer(bearer_path, replace=args.replace)
        else:
            secret = secrets.token_urlsafe(32)
            _write_secret_out(secret_out, secret)
            try:
                secret = new_bearer(bearer_path, replace=args.replace, secret=secret)
            except BaseException:
                secret_out.unlink(missing_ok=True)
                raise
    except (GateConfigError, OSError) as exc:
        print(f"configuration error: {terminal_text(exc)}", file=sys.stderr)
        return 2
    if secret_out is not None:
        print(secret_out)
        return 0
    print(secret, flush=True)
    print(
        "Give this bearer to the client now; it is not stored and is not shown again.",
        file=sys.stderr,
    )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``mcp-gate`` command."""
    gate = subparsers.add_parser(
        "mcp-gate",
        help="Serve or provision a stable-bearer gate in front of the HTTP MCP listener.",
    )
    actions = gate.add_subparsers(dest="mcp_gate_action", required=True)
    serve = actions.add_parser("serve", help="Run the gate until it is stopped.")
    serve.add_argument("--config", required=True, help="Owner-only gate configuration JSON.")
    serve.set_defaults(func=_cmd_serve)
    bearer = actions.add_parser(
        "new-bearer",
        help="Create a seat's stable bearer. The secret goes to a new owner-only "
        "--secret-out file, or to stdout with --print-secret.",
    )
    bearer.add_argument("--config", required=True, help="Owner-only gate configuration JSON.")
    bearer.add_argument("--seat", required=True, help="The PROJECT/seat named in the config.")
    bearer.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing record atomically, which revokes the old bearer.",
    )
    destination = bearer.add_mutually_exclusive_group()
    destination.add_argument(
        "--secret-out",
        help="Write the generated secret to this new owner-only file.",
    )
    destination.add_argument(
        "--print-secret",
        action="store_true",
        help="Print the generated secret once on stdout instead; terminal logs and CI "
        "output keep it.",
    )
    bearer.set_defaults(func=_cmd_new_bearer)
