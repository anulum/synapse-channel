# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest encryption key-file management CLI
"""Manage at-rest encryption key files: ``synapse encrypt-key generate/check`` and friends.

These are local file operations — they do not connect to the hub. ``generate``
writes a fresh owner-only 32-byte key (random, or scrypt-derived from a passphrase
with ``--from-passphrase``); ``generate-wrapped`` writes an envelope-encrypted key
whose passphrase can later be rotated with ``rewrap`` without re-encrypting any
data; ``check`` verifies an existing key file's ownership, mode, and length before
an encrypted workflow trusts it. The key file feeds
:class:`~synapse_channel.core.at_rest.AtRestCipher` for the storage-surface
encryption that builds on this foundation (see ``docs/at-rest-encryption``).

The wider ``encrypt-key`` family lives in sibling modules, one per domain, and
registers under the same subparser group here: hardware-backed wrapping
(:mod:`~synapse_channel.cli_encrypt_key_hardware`), threshold escrow
(:mod:`~synapse_channel.cli_encrypt_key_escrow`), attestation gating
(:mod:`~synapse_channel.cli_encrypt_key_attest`), and the at-rest profile
lifecycle (:mod:`~synapse_channel.cli_encrypt_key_profile`).
"""

from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable

from synapse_channel.cli_encrypt_key_attest import add_attestation_parsers
from synapse_channel.cli_encrypt_key_escrow import add_escrow_parsers
from synapse_channel.cli_encrypt_key_hardware import add_hardware_parsers
from synapse_channel.cli_encrypt_key_profile import add_profile_parsers
from synapse_channel.core.at_rest import (
    DEFAULT_SCRYPT_N,
    DEFAULT_SCRYPT_P,
    DEFAULT_SCRYPT_R,
    check_key_file,
    generate_key_file,
    generate_key_file_from_passphrase,
    generate_wrapped_key_file,
    rewrap_wrapped_key_file,
)


def _cmd_generate(
    args: argparse.Namespace,
    *,
    passphrase_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Create a fresh owner-only key file, refusing to overwrite an existing one.

    With ``--from-passphrase`` the key is derived from a prompted passphrase via
    scrypt, whose cost is tunable with ``--scrypt-n`` / ``--scrypt-r`` /
    ``--scrypt-p``; otherwise it is 32 random bytes. Either way the written file
    is a 32-byte owner-only key of record.
    """
    try:
        if args.from_passphrase:
            passphrase = passphrase_reader("At-rest passphrase: ")
            if passphrase != passphrase_reader("Confirm passphrase: "):
                print("passphrases do not match")
                return 2
            written = generate_key_file_from_passphrase(
                args.path, passphrase, n=args.scrypt_n, r=args.scrypt_r, p=args.scrypt_p
            )
        else:
            written = generate_key_file(args.path)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(f"synapse encrypt-key generate: {exc}")
        return 2
    print(f"wrote at-rest key (owner-only, 32 bytes): {written}")
    return 0


def _read_new_passphrase(
    reader: Callable[[str], str], *, prompt: str = "At-rest passphrase: "
) -> str | None:
    """Prompt for a passphrase twice, returning it, or ``None`` when the two entries differ."""
    passphrase = reader(prompt)
    if passphrase != reader("Confirm passphrase: "):
        return None
    return passphrase


def _cmd_generate_wrapped(
    args: argparse.Namespace,
    *,
    passphrase_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Create an envelope-encrypted (KEK-wrapped) key file from a prompted passphrase.

    The data key is random; the prompted passphrase derives (via scrypt) a key-encryption key that
    wraps it. Because the salt is kept, the passphrase can later be rotated with ``rewrap`` without
    re-encrypting any data — the envelope model an HSM-held key-encryption key plugs into.
    """
    passphrase = _read_new_passphrase(passphrase_reader)
    if passphrase is None:
        print("passphrases do not match")
        return 2
    try:
        written = generate_wrapped_key_file(
            args.path, passphrase, n=args.scrypt_n, r=args.scrypt_r, p=args.scrypt_p
        )
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(f"synapse encrypt-key generate-wrapped: {exc}")
        return 2
    print(f"wrote wrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_rewrap(
    args: argparse.Namespace,
    *,
    passphrase_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Rotate a wrapped key file's passphrase without changing the data key or data."""
    old_passphrase = passphrase_reader("Current at-rest passphrase: ")
    new_passphrase = _read_new_passphrase(passphrase_reader, prompt="New at-rest passphrase: ")
    if new_passphrase is None:
        print("passphrases do not match")
        return 2
    try:
        written = rewrap_wrapped_key_file(
            args.path,
            old_passphrase,
            new_passphrase,
            n=args.scrypt_n,
            r=args.scrypt_r,
            p=args.scrypt_p,
        )
    except (ValueError, OSError) as exc:
        print(f"synapse encrypt-key rewrap: {exc}")
        return 2
    print(f"rewrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Verify a key file is owner-only, regular, and full-length."""
    ok, reason = check_key_file(args.path)
    print(f"key file ok: {args.path}" if ok else f"key file problem: {reason}")
    return 0 if ok else 1


def _add_scrypt_args(parser: argparse.ArgumentParser) -> None:
    """Register the shared scrypt cost selectors for passphrase-based key commands."""
    parser.add_argument(
        "--scrypt-n",
        type=int,
        default=DEFAULT_SCRYPT_N,
        help=f"scrypt CPU/memory cost, a power of two (default {DEFAULT_SCRYPT_N}).",
    )
    parser.add_argument(
        "--scrypt-r",
        type=int,
        default=DEFAULT_SCRYPT_R,
        help=f"scrypt block-size parameter (default {DEFAULT_SCRYPT_R}).",
    )
    parser.add_argument(
        "--scrypt-p",
        type=int,
        default=DEFAULT_SCRYPT_P,
        help=f"scrypt parallelisation parameter (default {DEFAULT_SCRYPT_P}).",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``encrypt-key`` subparser group.

    The registration order matches the original single-module layout so
    ``--help`` output is unchanged: local key-file commands, then the
    hardware, escrow, and attestation families, then ``check``, then the
    profile lifecycle family.
    """
    encrypt_key = subparsers.add_parser(
        "encrypt-key", help="Manage at-rest encryption key files (generate/check)."
    )
    nested = encrypt_key.add_subparsers(dest="encrypt_key_command", required=True)

    generate = nested.add_parser("generate", help="Write a fresh owner-only 32-byte key file.")
    generate.add_argument("path", help="Destination key-file path (must not already exist).")
    generate.add_argument(
        "--from-passphrase",
        action="store_true",
        help="Derive the key from a prompted passphrase via scrypt instead of random bytes.",
    )
    _add_scrypt_args(generate)
    generate.set_defaults(func=_cmd_generate)

    generate_wrapped = nested.add_parser(
        "generate-wrapped",
        help="Write a passphrase-wrapped key file whose passphrase can be rotated later.",
    )
    generate_wrapped.add_argument(
        "path", help="Destination wrapped-key-file path (must not already exist)."
    )
    _add_scrypt_args(generate_wrapped)
    generate_wrapped.set_defaults(func=_cmd_generate_wrapped)

    rewrap = nested.add_parser(
        "rewrap",
        help="Rotate a wrapped key file's passphrase without re-encrypting any data.",
    )
    rewrap.add_argument("path", help="Existing wrapped-key-file path.")
    _add_scrypt_args(rewrap)
    rewrap.set_defaults(func=_cmd_rewrap)

    add_hardware_parsers(nested)
    add_escrow_parsers(nested)
    add_attestation_parsers(nested)

    check = nested.add_parser("check", help="Verify a key file's ownership, mode, and length.")
    check.add_argument("path", help="Key-file path to check.")
    check.set_defaults(func=_cmd_check)

    add_profile_parsers(nested)
