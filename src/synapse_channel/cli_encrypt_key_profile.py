# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest profile lifecycle CLI (inspect/migrate/rekey/backup/restore + SQLCipher)
"""At-rest profile lifecycle: ``synapse encrypt-key profile/migrate/rekey/…``.

These commands operate on the CONFIGURED runtime surfaces (event stores,
relay logs, A2A state, cursors, archive reports) as one profile: inspect it
(optionally failing closed on any plaintext surface), encrypt existing
plaintext files, rotate keys, and write/restore recovery bundles. The
SQLCipher pair migrates/rekeys a live hub event store offline (hub stopped);
resume cursors keep their sequence numbers.
"""

from __future__ import annotations

import argparse

from synapse_channel.core.at_rest import (
    AtRestCipher,
    AtRestProfileReport,
    AtRestSurface,
    backup_profile,
    full_profile_surfaces,
    inspect_profile,
    migrate_profile,
    rekey_profile,
    require_encrypted_profile,
    restore_profile_backup,
)
from synapse_channel.terminal_text import shell_long_option, terminal_text


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
        print(f"sqlcipher migrate problem: {terminal_text(exc)}")
        return 1
    print(
        f"sqlcipher migrated {terminal_text(result['rows'])} event(s): "
        f"{terminal_text(args.source)} -> {terminal_text(args.destination)}"
    )
    print(
        "start the hub with: synapse hub "
        f"{shell_long_option('--db', '<destination>')} "
        f"{shell_long_option('--db-key-file', '<key>')}"
    )
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
        print(f"sqlcipher rekey problem: {terminal_text(exc)}")
        return 1
    print(f"sqlcipher rekeyed: {terminal_text(result['path'])}")
    print(
        "start the hub with: synapse hub "
        f"{shell_long_option('--db', args.db)} "
        f"{shell_long_option('--db-key-file', args.new_key)}"
    )
    return 0


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


def add_profile_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the at-rest profile lifecycle and SQLCipher subcommands."""
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
