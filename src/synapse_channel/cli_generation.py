# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse generation head|read|publish|seal` command-line surface
"""``synapse generation``: inspect, publish, and seal a local generation chain.

External deployment adapters invoke these subcommands across a subprocess
boundary without importing the package. ``publish`` and ``seal`` exit ``0``
when the slot is created, ``1`` when the slot already exists (printing the
winning slot record so a refused seal sees ``applied``), and ``2`` when an
input or chain directory is invalid or corrupt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from synapse_channel.core.generation_chain import (
    STATUS_CREATED,
    GenerationChainError,
    chain_head,
    publish_generation,
    read_generation,
    seal_generation,
)

EXIT_OK = 0
"""Slot created (publish/seal), slot read (read), or chain verified (head)."""

EXIT_REFUSED = 1
"""Slot already taken (publish/seal) or requested generation absent (read)."""

EXIT_INVALID = 2
"""Invalid argument, non-private or network/FUSE directory, gap, or corrupt record."""

_DECIMAL = re.compile(r"0|[1-9][0-9]{0,19}")


def _parse_uint64(raw: str, *, allow_zero: bool, flag: str) -> int:
    """Parse a canonical unsigned decimal string without sign or leading zeros."""
    if not _DECIMAL.fullmatch(raw):
        raise GenerationChainError(f"{flag} must be a canonical non-negative integer")
    value = int(raw)
    if not allow_zero and value == 0:
        raise GenerationChainError(f"{flag} must be at least 1")
    return value


def _emit_json(payload: Mapping[str, object]) -> None:
    """Write one deterministic JSON object line to standard output."""
    print(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")))


def _cmd_head(args: argparse.Namespace) -> int:
    """Print ``{"head": N}`` for a verified gap-free chain."""
    head = chain_head(args.chain)
    _emit_json({"head": head})
    return EXIT_OK


def _cmd_read(args: argparse.Namespace) -> int:
    """Print the canonical record at ``--generation``, or exit 1 when absent."""
    number = _parse_uint64(args.generation, allow_zero=False, flag="--generation")
    record = read_generation(args.chain, number)
    if record is None:
        _emit_json({"generation": number, "status": "absent"})
        return EXIT_REFUSED
    _emit_json(record.to_dict())
    return EXIT_OK


def _cmd_publish(args: argparse.Namespace) -> int:
    """Publish ``applied`` at ``--base + 1``; exit 0 created, 1 refused."""
    base = _parse_uint64(args.base, allow_zero=True, flag="--base")
    outcome = publish_generation(
        args.chain,
        base=base,
        operation=args.operation,
        attempt=args.attempt,
        artefact=args.artefact,
    )
    _emit_json({"record": outcome.record.to_dict(), "status": outcome.status})
    return EXIT_OK if outcome.status == STATUS_CREATED else EXIT_REFUSED


def _cmd_seal(args: argparse.Namespace) -> int:
    """Publish ``sealed`` at ``--base + 1`` carrying the base artefact forward."""
    base = _parse_uint64(args.base, allow_zero=True, flag="--base")
    outcome = seal_generation(
        args.chain,
        base=base,
        operation=args.operation,
        attempt=args.attempt,
    )
    _emit_json({"record": outcome.record.to_dict(), "status": outcome.status})
    return EXIT_OK if outcome.status == STATUS_CREATED else EXIT_REFUSED


def _dispatch(args: argparse.Namespace) -> int:
    """Execute the selected subcommand and map domain or OS errors to exit 2."""
    try:
        return int(args.run(args))
    except (GenerationChainError, OSError) as exc:
        print(f"generation: {exc}", file=sys.stderr)
        return EXIT_INVALID


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse generation`` with ``head``, ``read``, ``publish``, and ``seal``.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        Top-level ``synapse`` subcommand group.
    """
    group = subparsers.add_parser(
        "generation",
        help="Read, publish, and seal a local deployment generation chain.",
    )
    nested = group.add_subparsers(dest="generation_command", required=True)

    head = nested.add_parser("head", help="Verify the chain and print its head generation.")
    head.add_argument("--chain", required=True, type=Path, help="Owner-only chain directory.")
    head.set_defaults(func=_dispatch, run=_cmd_head)

    read = nested.add_parser("read", help="Read and strictly verify one generation slot.")
    read.add_argument("--chain", required=True, type=Path, help="Owner-only chain directory.")
    read.add_argument("--generation", required=True, help="Generation number (>= 1).")
    read.set_defaults(func=_dispatch, run=_cmd_read)

    publish = nested.add_parser("publish", help="Publish an applied generation from a base.")
    publish.add_argument("--chain", required=True, type=Path, help="Owner-only chain directory.")
    publish.add_argument("--base", required=True, help="Declared pre-state generation (>= 0).")
    publish.add_argument("--operation", required=True, help="Declared operation identifier.")
    publish.add_argument("--attempt", required=True, help="Attempt identifier.")
    publish.add_argument("--artefact", required=True, help="64-hex SHA-256 of the artefact.")
    publish.set_defaults(func=_dispatch, run=_cmd_publish)

    seal = nested.add_parser(
        "seal",
        help="Seal slot base + 1 carrying generation base's artefact forward.",
    )
    seal.add_argument("--chain", required=True, type=Path, help="Owner-only chain directory.")
    seal.add_argument("--base", required=True, help="Declared pre-state generation (>= 0).")
    seal.add_argument("--operation", required=True, help="Declared operation identifier.")
    seal.add_argument("--attempt", required=True, help="Attempt identifier to seal.")
    seal.set_defaults(func=_dispatch, run=_cmd_seal)
