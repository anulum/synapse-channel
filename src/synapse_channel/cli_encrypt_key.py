# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest encryption key-file management CLI
"""Manage at-rest encryption key files: ``synapse encrypt-key generate/check``.

These are local file operations — they do not connect to the hub. ``generate``
writes a fresh owner-only 32-byte key; ``check`` verifies an existing key file's
ownership, mode, and length before an encrypted workflow trusts it. The key file
feeds :class:`~synapse_channel.core.at_rest.AtRestCipher` for the storage-surface
encryption that builds on this foundation (see ``docs/at-rest-encryption``).
"""

from __future__ import annotations

import argparse

from synapse_channel.core.at_rest import check_key_file, generate_key_file


def _cmd_generate(args: argparse.Namespace) -> int:
    """Create a fresh owner-only key file, refusing to overwrite an existing one."""
    try:
        written = generate_key_file(args.path)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    print(f"wrote at-rest key (owner-only, 32 bytes): {written}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Verify a key file is owner-only, regular, and full-length."""
    ok, reason = check_key_file(args.path)
    print(f"key file ok: {args.path}" if ok else f"key file problem: {reason}")
    return 0 if ok else 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``encrypt-key`` subparser group."""
    encrypt_key = subparsers.add_parser(
        "encrypt-key", help="Manage at-rest encryption key files (generate/check)."
    )
    nested = encrypt_key.add_subparsers(dest="encrypt_key_command", required=True)

    generate = nested.add_parser("generate", help="Write a fresh owner-only 32-byte key file.")
    generate.add_argument("path", help="Destination key-file path (must not already exist).")
    generate.set_defaults(func=_cmd_generate)

    check = nested.add_parser("check", help="Verify a key file's ownership, mode, and length.")
    check.add_argument("path", help="Key-file path to check.")
    check.set_defaults(func=_cmd_check)
