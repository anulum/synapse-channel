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

A file of the earlier schema is read as a legacy inventory: its rows are
printed and marked, and none of them may run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from synapse_channel.cli_ops_qualification import add_parsers as add_qualification_parsers
from synapse_channel.core.protocol import loads_bounded
from synapse_channel.core.terminal_text import terminal_text
from synapse_channel.operations.catalogue import (
    Catalogue,
    LegacyInventory,
    admission_refusal,
    read_catalogue,
)
from synapse_channel.operations.row import ArgumentBinding, OperationRow
from synapse_channel.operations.spec import OperationSpec, OperationSpecError

MAX_CATALOGUE_BYTES = 4 * 1024 * 1024
"""Largest catalogue file read."""


def default_catalogue_path() -> Path:
    """Return where this host's catalogue file is looked for."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "synapse" / "operations" / "catalogue.json"


def load_catalogue(path: Path) -> Catalogue | LegacyInventory:
    """Read a catalogue file of the current or of the earlier schema.

    Raises
    ------
    OperationSpecError
        The file is too large, is not JSON within the nesting bound, or is
        not a catalogue.
    OSError
        The file cannot be read.
    """
    with path.open("rb") as handle:
        raw = handle.read(MAX_CATALOGUE_BYTES + 1)
    if len(raw) > MAX_CATALOGUE_BYTES:
        raise OperationSpecError("the catalogue file is larger than the limit")
    try:
        data = loads_bounded(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OperationSpecError("the catalogue file is not JSON") from exc
    return read_catalogue(data)


def _state(row: OperationRow) -> str:
    if row.stale:
        return "binding changed since, not run"
    return "not run" if admission_refusal(row, row.fingerprint) else "may run"


def _line(row: OperationRow | OperationSpec) -> str:
    # Every field here is a token or a vocabulary word that the description
    # already refused control characters in; free text appears in the details only.
    if isinstance(row, OperationSpec):
        return (
            f"{row.host} {row.vendor} {row.client_version} {row.os}  {row.operation}  "
            f"{row.transport}/{row.scope}  {row.qualification} (earlier schema, not run)"
        )
    variant = "" if row.variant == "default" else f"#{row.variant}"
    return (
        f"{row.host} {row.vendor} {row.component} {row.component_version} {row.os}  "
        f"{row.operation}{variant}  {row.transport}/{row.scope}  {row.display_class}  "
        f"{row.qualification} ({_state(row)})"
    )


def _argument(argument: ArgumentBinding) -> str:
    kind = f"list of at most {argument.max_items} {argument.type}" if argument.max_items else ""
    need = "required" if argument.required else "optional"
    choices = f" one of {', '.join(argument.choices)}" if argument.choices else ""
    low = "" if argument.minimum is None else f" from {argument.minimum}"
    high = "" if argument.maximum is None else f" to {argument.maximum}"
    return (
        f"argument {argument.name}: {kind or argument.type}, {need}{choices}{low}{high}; "
        f"{argument.placement} {argument.native}"
    )


def _details(row: OperationRow) -> list[str]:
    binding = row.binding
    invoked = (
        " ".join([binding.executable, *binding.fixed]) if binding.executable else binding.method
    )
    permissions = ", ".join(row.required_authorities) or "none can be granted"
    lines = [
        f"operation: {row.operation} (variant {row.variant})",
        f"component: {row.vendor} {row.component} {row.component_version} on {row.os} at {row.host}"
        + (f" (reports: {row.version_printed})" if row.version_printed else ""),
        f"transport: {row.transport}; scope: {row.scope}",
        f"native name: {binding.native_name}",
        f"invoked as: {invoked}",
        f"effects: {', '.join(row.effects)}; every permission needed: {permissions}"
        + ("; a named person must consent" if row.needs_human_principal else ""),
        f"output: {row.output_mode}"
        + (f" ({row.output_schema})" if row.output_schema else "")
        + (f"; discloses: {', '.join(row.disclosures)}" if row.disclosures else "")
        + (f"; projection: {row.output_projection}" if row.output_projection else ""),
        f"interactive: {row.interactive}; idempotent: {row.idempotent}; "
        f"needs restart: {'yes' if row.needs_restart else 'no'}",
        f"qualification: {row.qualification}"
        + (f" (evidence: {row.evidence})" if row.evidence else "")
        + f"; {_state(row)}",
    ]
    lines.extend(_argument(argument) for argument in row.arguments)
    lines.extend(
        f"the component may ask: {item.request}; ended by: {item.disposition}"
        for item in row.obligations
    )
    lines.extend(f"side effect: {effect}" for effect in row.side_effects)
    return [terminal_text(line) for line in lines]


def _legacy_details(row: OperationSpec) -> list[str]:
    lines = [
        f"operation: {row.operation}",
        f"client: {row.vendor} {row.client_version} on {row.os} at {row.host}",
        f"transport: {row.transport}; scope: {row.scope}",
        f"permission named: {row.required_authority}",
        f"qualification stated: {row.qualification}; earlier schema: listed, and not run",
    ]
    lines.extend(f"side effect: {effect}" for effect in row.side_effects)
    return [terminal_text(line) for line in lines]


def _selected(
    catalogue: Catalogue | LegacyInventory, args: argparse.Namespace
) -> tuple[OperationRow | OperationSpec, ...]:
    if isinstance(catalogue, LegacyInventory):
        legacy = catalogue.rows(host=args.host, vendor=args.vendor)
        return tuple(row for row in legacy if args.transport in (None, row.transport))
    return catalogue.rows(host=args.host, vendor=args.vendor, transport=args.transport)


def _cmd_ops(args: argparse.Namespace) -> int:
    """Dispatch ``synapse ops``."""
    path = Path(args.catalogue) if args.catalogue else default_catalogue_path()
    try:
        catalogue = load_catalogue(path)
    except OSError:
        print("synapse ops: cannot read the catalogue file", file=sys.stderr)
        return 2
    except OperationSpecError as exc:
        print(f"synapse ops: cannot use the catalogue: {terminal_text(str(exc))}", file=sys.stderr)
        return 2
    rows = _selected(catalogue, args)
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
        details = _legacy_details(row) if isinstance(row, OperationSpec) else _details(row)
        print("\n".join(details))
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
    describe.add_argument("operation", help="Dotted operation name, e.g. plugin.marketplace.list.")
    for command in (listing, describe):
        command.add_argument("--catalogue", default=None, help="Catalogue file to read.")
        command.add_argument("--host", default=None, help="Only rows of this host.")
        command.add_argument("--vendor", default=None, help="Only rows of this vendor.")
        command.add_argument("--transport", default=None, help="Only rows of this transport.")
        command.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        command.set_defaults(func=_cmd_ops)
    add_qualification_parsers(commands)
