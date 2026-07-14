# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse sqlcipher` CLI: live event-store page encryption ops
"""``synapse sqlcipher`` — SQLCipher page-encryption operations for the hub store.

The stock hub uses ordinary SQLite. With the optional ``[sqlcipher]`` extra the
live event store can use page encryption so main DB + WAL stay ciphertext on
disk. This command group is the operator surface for offline maintenance:

* ``rekey`` — rotate the page key in place via SQLCipher ``PRAGMA rekey``
  (hub stopped; old key opens, new key is written, old key is verified closed).
* ``migrate`` — copy a plaintext store into a new encrypted destination.

Key material is always an owner-only raw key file (``synapse encrypt-key
generate``). Passphrase cost for passphrase-derived keys is tuned on
``encrypt-key generate --from-passphrase`` via ``--scrypt-n`` / ``--scrypt-r`` /
``--scrypt-p``.
"""

from __future__ import annotations

import argparse
import sys

from synapse_channel.terminal_text import shell_long_option, terminal_text


def _cmd_rekey(args: argparse.Namespace) -> int:
    """Rotate the SQLCipher page key using ``PRAGMA rekey`` (hub stopped)."""
    from synapse_channel.core.persistence_sqlcipher import (
        SqlCipherKeyError,
        SqlCipherUnavailableError,
        rekey_sqlcipher_store,
    )

    try:
        result = rekey_sqlcipher_store(
            args.db,
            old_key_file=args.old_key,
            new_key_file=args.new_key,
        )
    except (
        ValueError,
        FileNotFoundError,
        SqlCipherUnavailableError,
        SqlCipherKeyError,
    ) as exc:
        print(f"synapse sqlcipher rekey: {terminal_text(exc)}", file=sys.stderr)
        return 1
    print(f"sqlcipher rekeyed: {terminal_text(result['path'])}")
    print(
        "start the hub with: synapse hub "
        f"{shell_long_option('--db', args.db)} "
        f"{shell_long_option('--db-key-file', args.new_key)}"
    )
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Copy a plaintext event store into a new SQLCipher-encrypted destination."""
    from synapse_channel.core.persistence_sqlcipher import (
        SqlCipherKeyError,
        SqlCipherUnavailableError,
        migrate_plaintext_to_sqlcipher,
    )

    try:
        result = migrate_plaintext_to_sqlcipher(
            source=args.source,
            destination=args.destination,
            key_file=args.key,
        )
    except (
        ValueError,
        FileNotFoundError,
        SqlCipherUnavailableError,
        SqlCipherKeyError,
        OSError,
    ) as exc:
        print(f"synapse sqlcipher migrate: {terminal_text(exc)}", file=sys.stderr)
        return 1
    print(
        f"sqlcipher migrated {terminal_text(result['rows'])} event(s): "
        f"{terminal_text(args.source)} -> {terminal_text(args.destination)}"
    )
    print(
        "start the hub with: synapse hub "
        f"{shell_long_option('--db', args.destination)} "
        f"{shell_long_option('--db-key-file', args.key)}"
    )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sqlcipher`` command group."""
    parser = subparsers.add_parser(
        "sqlcipher",
        help=(
            "SQLCipher page-encryption ops for the hub event store "
            "(rekey via PRAGMA rekey; migrate plaintext → encrypted)."
        ),
    )
    group = parser.add_subparsers(dest="sqlcipher_command", required=True)

    rekey = group.add_parser(
        "rekey",
        help=(
            "Rotate the SQLCipher page key in place using PRAGMA rekey "
            "(requires synapse-channel[sqlcipher]; hub must be stopped)."
        ),
    )
    rekey.add_argument(
        "--db",
        required=True,
        help="Existing encrypted event-store path (synapse hub --db).",
    )
    rekey.add_argument(
        "--old-key",
        required=True,
        help="Current owner-only raw key file that opens the store.",
    )
    rekey.add_argument(
        "--new-key",
        required=True,
        help="Replacement owner-only raw key file (must differ from --old-key).",
    )
    rekey.set_defaults(func=_cmd_rekey)

    migrate = group.add_parser(
        "migrate",
        help=(
            "Copy a plaintext hub event store into a new SQLCipher-encrypted file "
            "(requires synapse-channel[sqlcipher]; hub must be stopped)."
        ),
    )
    migrate.add_argument(
        "--key",
        required=True,
        help="Owner-only raw key file for the encrypted destination.",
    )
    migrate.add_argument(
        "--source",
        required=True,
        help="Existing plaintext event-store path.",
    )
    migrate.add_argument(
        "--destination",
        required=True,
        help="New encrypted event-store path (must not already exist).",
    )
    migrate.set_defaults(func=_cmd_migrate)
