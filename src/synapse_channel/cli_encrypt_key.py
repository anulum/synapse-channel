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
"""

from __future__ import annotations

import argparse
import getpass
import os
from collections.abc import Callable

from synapse_channel.core.at_rest import (
    DEFAULT_SCRYPT_N,
    DEFAULT_SCRYPT_P,
    DEFAULT_SCRYPT_R,
    AtRestCipher,
    AtRestProfileReport,
    AtRestSurface,
    backup_profile,
    check_key_file,
    full_profile_surfaces,
    generate_key_file,
    generate_key_file_from_passphrase,
    generate_wrapped_key_file,
    inspect_profile,
    migrate_profile,
    rekey_profile,
    require_encrypted_profile,
    restore_profile_backup,
    rewrap_wrapped_key_file,
)
from synapse_channel.core.at_rest_pkcs11 import DEFAULT_KEK_LABEL
from synapse_channel.core.at_rest_tpm2 import DEFAULT_TPM2_TCTI


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


def _cmd_generate_wrapped_pkcs11(
    args: argparse.Namespace,
    *,
    pin_reader: Callable[[str], str] = getpass.getpass,
) -> int:
    """Create a key file wrapped by a key-encryption key held on a PKCS#11 token.

    The data key is random and wrapped on the token (YubiKey PIV, cloud/network HSM, or SoftHSM);
    the token key never leaves the device. The module path comes from ``--pkcs11-module`` or the
    ``PKCS11_MODULE`` environment variable; the PIN from ``PKCS11_PIN`` or an interactive prompt.
    """
    from synapse_channel.core.at_rest_pkcs11 import generate_wrapped_key_file_pkcs11

    module_path = args.pkcs11_module or os.environ.get("PKCS11_MODULE")
    if not module_path:
        print(
            "synapse encrypt-key generate-wrapped-pkcs11: "
            "a PKCS#11 module is required via --pkcs11-module or PKCS11_MODULE"
        )
        return 2
    pin = os.environ.get("PKCS11_PIN") or pin_reader("PKCS#11 user PIN: ")
    try:
        written = generate_wrapped_key_file_pkcs11(
            args.path,
            module_path=module_path,
            token_label=args.token_label,
            pin=pin,
            key_label=args.key_label,
            create_kek=args.create_kek,
        )
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"synapse encrypt-key generate-wrapped-pkcs11: {exc}")
        return 2
    print(f"wrote PKCS#11-wrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_generate_wrapped_tpm2(args: argparse.Namespace) -> int:
    """Create a key file wrapped by a key-encryption key rooted in a TPM 2.0 device.

    The data key is random and wrapped with RSA-OAEP against a decrypt-only primary derived inside
    the TPM; the RSA private key never leaves the chip. The TPM is reached through ``--tcti`` (or
    the ``TPM2_TCTI`` environment variable), defaulting to the in-kernel resource-managed device.
    """
    from synapse_channel.core.at_rest_tpm2 import generate_wrapped_key_file_tpm2

    tcti = args.tcti or os.environ.get("TPM2_TCTI") or DEFAULT_TPM2_TCTI
    try:
        written = generate_wrapped_key_file_tpm2(args.path, tcti=tcti)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"synapse encrypt-key generate-wrapped-tpm2: {exc}")
        return 2
    print(f"wrote TPM-wrapped at-rest key (owner-only): {written}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Verify a key file is owner-only, regular, and full-length."""
    ok, reason = check_key_file(args.path)
    print(f"key file ok: {args.path}" if ok else f"key file problem: {reason}")
    return 0 if ok else 1


def _surfaces(args: argparse.Namespace) -> tuple[AtRestSurface, ...]:
    """Build at-rest profile surfaces from shared CLI flags."""
    return full_profile_surfaces(
        sqlite_event_stores=args.sqlite_db,
        relay_logs=args.relay_log,
        a2a_state_files=args.a2a_state_file,
        cursor_files=args.cursor,
        archive_outputs=args.archive_report,
    )


def _print_report(report: AtRestProfileReport) -> None:
    """Print a compact at-rest profile inspection report."""
    print(f"surfaces: {report.total}")
    print(f"existing: {report.existing}")
    print(f"missing: {report.missing}")
    print(f"encrypted: {report.encrypted}")
    print(f"plaintext: {report.plaintext}")
    for status in report.statuses:
        if not status.exists:
            print(f"missing {status.surface.role}: {status.surface.path}")
        elif not status.encrypted or not status.decryptable:
            print(f"problem {status.surface.role}: {status.surface.path} ({status.reason})")


def _cmd_profile(args: argparse.Namespace) -> int:
    """Inspect the configured at-rest profile and optionally require encryption."""
    try:
        cipher = AtRestCipher.from_key_file(args.key)
        surfaces = _surfaces(args)
        report = (
            require_encrypted_profile(surfaces, cipher)
            if args.require_encrypted
            else inspect_profile(surfaces, cipher)
        )
    except ValueError as exc:
        print(f"at-rest profile problem: {exc}")
        return 1
    _print_report(report)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Encrypt existing plaintext profile surfaces with the configured key."""
    try:
        cipher = AtRestCipher.from_key_file(args.key)
        result = migrate_profile(_surfaces(args), cipher, backup_dir=args.backup_dir)
    except ValueError as exc:
        print(f"at-rest migration problem: {exc}")
        return 1
    print(f"encrypted {result.changed} file(s); skipped {result.skipped} file(s)")
    return 0


def _cmd_rekey(args: argparse.Namespace) -> int:
    """Rotate encrypted profile surfaces from one key file to another."""
    try:
        old_cipher = AtRestCipher.from_key_file(args.old_key)
        new_cipher = AtRestCipher.from_key_file(args.new_key)
        result = rekey_profile(
            _surfaces(args),
            old_cipher,
            new_cipher,
            backup_dir=args.backup_dir,
        )
    except ValueError as exc:
        print(f"at-rest rekey problem: {exc}")
        return 1
    print(f"re-encrypted {result.changed} file(s); skipped {result.skipped} file(s)")
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    """Write a recovery bundle manifest for encrypted profile surfaces."""
    try:
        cipher = AtRestCipher.from_key_file(args.key)
        manifest = backup_profile(_surfaces(args), args.backup_dir, cipher)
    except ValueError as exc:
        print(f"at-rest backup problem: {exc}")
        return 1
    print(f"backup manifest: {manifest}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    """Restore encrypted profile surfaces from a recovery bundle manifest."""
    try:
        cipher = AtRestCipher.from_key_file(args.key)
        result = restore_profile_backup(args.manifest, cipher)
    except ValueError as exc:
        print(f"at-rest restore problem: {exc}")
        return 1
    print(f"restored {result.changed} file(s)")
    return 0


def _cmd_migrate_sqlcipher(args: argparse.Namespace) -> int:
    """Offline-copy a plaintext hub event store into a new SQLCipher database.

    Stop the hub first. Destination must not exist. Resume cursors keep their
    sequence numbers.
    """
    from synapse_channel.core.persistence_sqlcipher import (
        SqlCipherUnavailableError,
        migrate_plaintext_to_sqlcipher,
    )

    try:
        result = migrate_plaintext_to_sqlcipher(
            args.source,
            args.destination,
            key_file=args.key,
        )
    except (ValueError, FileNotFoundError, FileExistsError, SqlCipherUnavailableError) as exc:
        print(f"sqlcipher migrate problem: {exc}")
        return 1
    print(f"sqlcipher migrated {result['rows']} event(s): {args.source} -> {args.destination}")
    print("start the hub with: synapse hub --db <destination> --db-key-file <key>")
    return 0


def _cmd_rekey_sqlcipher(args: argparse.Namespace) -> int:
    """Rotate the SQLCipher page key for a live event-store file (hub stopped)."""
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
        print(f"sqlcipher rekey problem: {exc}")
        return 1
    print(f"sqlcipher rekeyed: {result['path']}")
    print(f"start the hub with: synapse hub --db {args.db} --db-key-file {args.new_key}")
    return 0


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


def _add_surface_args(parser: argparse.ArgumentParser) -> None:
    """Register the shared at-rest profile surface selectors."""
    parser.add_argument(
        "--sqlite-db",
        action="append",
        default=[],
        help="SQLite event-store path; includes -wal and -shm sidecars.",
    )
    parser.add_argument("--relay-log", action="append", default=[], help="Relay NDJSON log path.")
    parser.add_argument(
        "--a2a-state-file",
        action="append",
        default=[],
        help="Agent2Agent bridge state-file path.",
    )
    parser.add_argument(
        "--cursor",
        action="append",
        default=[],
        help="Relay or ingest cursor path.",
    )
    parser.add_argument(
        "--archive-report",
        action="append",
        default=[],
        help="Compaction/archive/postmortem report output path.",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``encrypt-key`` subparser group."""
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

    pkcs11 = nested.add_parser(
        "generate-wrapped-pkcs11",
        help="Write a key file wrapped by a key-encryption key on a PKCS#11 token (YubiKey/HSM).",
    )
    pkcs11.add_argument("path", help="Destination wrapped-key-file path (must not already exist).")
    pkcs11.add_argument(
        "--pkcs11-module",
        default=None,
        help="Path to the PKCS#11 module (.so/.dll), or set the PKCS11_MODULE env var.",
    )
    pkcs11.add_argument(
        "--token-label",
        required=True,
        help="Label of the token that holds (or will hold) the key-encryption key.",
    )
    pkcs11.add_argument(
        "--key-label",
        default=DEFAULT_KEK_LABEL,
        help=f"Label of the token key-encryption key object (default {DEFAULT_KEK_LABEL!r}).",
    )
    pkcs11.add_argument(
        "--no-create-kek",
        dest="create_kek",
        action="store_false",
        help="Fail if the key-encryption key is absent instead of generating it on the token.",
    )
    pkcs11.set_defaults(func=_cmd_generate_wrapped_pkcs11, create_kek=True)

    tpm2 = nested.add_parser(
        "generate-wrapped-tpm2",
        help="Write a key file wrapped by a key-encryption key rooted in a TPM 2.0 device.",
    )
    tpm2.add_argument("path", help="Destination wrapped-key-file path (must not already exist).")
    tpm2.add_argument(
        "--tcti",
        default=None,
        help=(
            "TPM transmission interface (e.g. device:/dev/tpmrm0), or set the TPM2_TCTI env var "
            f"(default {DEFAULT_TPM2_TCTI!r})."
        ),
    )
    tpm2.set_defaults(func=_cmd_generate_wrapped_tpm2)

    check = nested.add_parser("check", help="Verify a key file's ownership, mode, and length.")
    check.add_argument("path", help="Key-file path to check.")
    check.set_defaults(func=_cmd_check)

    profile = nested.add_parser(
        "profile",
        help="Inspect encrypted runtime surfaces and fail closed when requested.",
    )
    profile.add_argument("--key", required=True, help="Owner-only raw key file.")
    profile.add_argument(
        "--require-encrypted",
        action="store_true",
        help="Return non-zero if any existing selected surface is plaintext or unreadable.",
    )
    _add_surface_args(profile)
    profile.set_defaults(func=_cmd_profile)

    migrate = nested.add_parser(
        "migrate",
        help="Encrypt existing plaintext runtime surfaces with an owner-only key file.",
    )
    migrate.add_argument("--key", required=True, help="Owner-only raw key file.")
    migrate.add_argument("--backup-dir", help="Optional owner-only backup directory for originals.")
    _add_surface_args(migrate)
    migrate.set_defaults(func=_cmd_migrate)

    rekey = nested.add_parser(
        "rekey",
        help="Rotate encrypted runtime surfaces from one key file to another.",
    )
    rekey.add_argument("--old-key", required=True, help="Current owner-only raw key file.")
    rekey.add_argument("--new-key", required=True, help="Replacement owner-only raw key file.")
    rekey.add_argument(
        "--backup-dir",
        help="Optional owner-only backup directory for old envelopes.",
    )
    _add_surface_args(rekey)
    rekey.set_defaults(func=_cmd_rekey)

    backup = nested.add_parser(
        "backup",
        help="Copy encrypted runtime surfaces into a recovery bundle manifest.",
    )
    backup.add_argument("--key", required=True, help="Owner-only raw key file.")
    backup.add_argument("--backup-dir", required=True, help="Owner-only backup bundle directory.")
    _add_surface_args(backup)
    backup.set_defaults(func=_cmd_backup)

    restore = nested.add_parser(
        "restore",
        help="Restore encrypted runtime surfaces from a recovery bundle manifest.",
    )
    restore.add_argument("--key", required=True, help="Owner-only raw key file.")
    restore.add_argument("--manifest", required=True, help="Backup manifest written by backup.")
    restore.set_defaults(func=_cmd_restore)

    migrate_sqlcipher = nested.add_parser(
        "migrate-sqlcipher",
        help=(
            "Offline-copy a plaintext hub --db event store into a new SQLCipher database "
            "(requires synapse-channel[sqlcipher]; hub must be stopped)."
        ),
    )
    migrate_sqlcipher.add_argument("--key", required=True, help="Owner-only raw key file.")
    migrate_sqlcipher.add_argument(
        "--source",
        required=True,
        help="Existing plaintext event-store path (synapse hub --db).",
    )
    migrate_sqlcipher.add_argument(
        "--destination",
        required=True,
        help="New encrypted database path (must not already exist).",
    )
    migrate_sqlcipher.set_defaults(func=_cmd_migrate_sqlcipher)

    rekey_sqlcipher = nested.add_parser(
        "rekey-sqlcipher",
        help=(
            "Rotate the SQLCipher page key for an existing encrypted hub --db "
            "(requires synapse-channel[sqlcipher]; hub must be stopped)."
        ),
    )
    rekey_sqlcipher.add_argument(
        "--db",
        required=True,
        help="Existing encrypted event-store path (synapse hub --db).",
    )
    rekey_sqlcipher.add_argument(
        "--old-key",
        required=True,
        help="Current owner-only raw key file that opens the store.",
    )
    rekey_sqlcipher.add_argument(
        "--new-key",
        required=True,
        help="Replacement owner-only raw key file (must differ from --old-key).",
    )
    rekey_sqlcipher.set_defaults(func=_cmd_rekey_sqlcipher)
