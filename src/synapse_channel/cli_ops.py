# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — command line for reading the operations catalogue
"""``synapse ops list`` and ``synapse ops describe``.

Both read a catalogue file on this host and print what it holds. Nothing is
run and nothing is sent: these commands show which operations of which vendor
client are known and how well. Every printed value comes from a file and is
made safe for a terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from synapse_channel.core.terminal_text import terminal_text
from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.spec import OperationSpec, OperationSpecError

MAX_CATALOGUE_BYTES = 4 * 1024 * 1024
"""Largest catalogue file read."""


def default_catalogue_path() -> Path:
    """Return where this host's catalogue file is looked for."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "operations" / "catalogue.json"


def load_catalogue(path: Path) -> Catalogue:
    """Read a catalogue file.

    Raises
    ------
    OperationSpecError
        The file is too large, is not JSON, or is not a catalogue.
    OSError
        The file cannot be read.
    """
    with path.open("rb") as handle:
        raw = handle.read(MAX_CATALOGUE_BYTES + 1)
    if len(raw) > MAX_CATALOGUE_BYTES:
        raise OperationSpecError("the catalogue file is larger than the limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OperationSpecError("the catalogue file is not JSON") from exc
    return Catalogue.from_json(data)


def _line(row: OperationSpec) -> str:
    # Every field here is a token or a vocabulary word that the description
    # already refused control characters in; free text appears in _details only.
    run = "may run" if row.runnable else "not run"
    return (
        f"{row.host} {row.vendor} {row.client_version} {row.os}  {row.operation}  "
        f"{row.transport}/{row.scope}  {row.qualification} ({run})"
    )


def _details(row: OperationSpec) -> list[str]:
    lines = [
        f"operation: {row.operation}",
        f"client: {row.vendor} {row.client_version} on {row.os} at {row.host}",
        f"transport: {row.transport}; scope: {row.scope}",
        f"permission needed: {row.required_authority}",
        f"interactive: {row.interactive}; idempotent: {row.idempotent}; "
        f"needs restart: {'yes' if row.needs_restart else 'no'}",
        f"qualification: {row.qualification}"
        + (f" (evidence: {row.evidence})" if row.evidence else "")
        + ("; may run" if row.runnable else "; listed, and not run"),
    ]
    for argument in row.arguments:
        need = "required" if argument.required else "optional"
        choices = f" one of {', '.join(argument.choices)}" if argument.choices else ""
        lines.append(f"argument {argument.name}: {argument.type}, {need}{choices}")
    lines.extend(f"side effect: {effect}" for effect in row.side_effects)
    return [terminal_text(line) for line in lines]


def _cmd_ops(args: argparse.Namespace) -> int:
    """Dispatch ``synapse ops``."""
    path = Path(args.catalogue) if args.catalogue else default_catalogue_path()
    try:
        catalogue = load_catalogue(path)
    except (OSError, OperationSpecError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else str(exc)
        print(
            f"synapse ops: cannot use the catalogue: {terminal_text(str(reason))}", file=sys.stderr
        )
        return 2
    rows = catalogue.rows(host=args.host, vendor=args.vendor)
    if args.ops_command == "describe":
        rows = tuple(row for row in rows if row.operation == args.operation)
        if not rows:
            print(f"synapse ops: {terminal_text(args.operation)} is not listed", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps([row.to_json() for row in rows], indent=2, sort_keys=True))
        return 0
    if args.ops_command == "list":
        print(f"{len(rows)} operation(s)")
        for row in rows:
            print(_line(row))
        return 0
    for index, row in enumerate(rows):
        if index:
            print()
        print("\n".join(_details(row)))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``ops``."""
    ops = subparsers.add_parser(
        "ops",
        help="Read the catalogue of operations that vendor clients offer on this host.",
    )
    commands = ops.add_subparsers(dest="ops_command", required=True)
    listing = commands.add_parser("list", help="Print every listed operation, one per line.")
    describe = commands.add_parser("describe", help="Print one operation in full.")
    describe.add_argument("operation", help="Dotted operation name, e.g. session.queue_message.")
    for command in (listing, describe):
        command.add_argument("--catalogue", default=None, help="Catalogue file to read.")
        command.add_argument("--host", default=None, help="Only rows of this host.")
        command.add_argument("--vendor", default=None, help="Only rows of this vendor.")
        command.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        command.set_defaults(func=_cmd_ops)
