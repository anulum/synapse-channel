# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — threshold key-escrow CLI (Shamir split/recover)
"""Threshold escrow for at-rest keys: ``synapse encrypt-key escrow-split/recover``.

A raw 32-byte key file splits into Shamir shares so that any ``threshold`` of
them recovers the key while fewer reveal nothing; recovery refuses to
overwrite an existing key file. Escrow protects against key loss without
creating a single custodian who can read the data alone.
"""

from __future__ import annotations

import argparse


def _cmd_escrow_split(args: argparse.Namespace) -> int:
    """Split a raw at-rest key file into threshold (Shamir) escrow shares."""
    from synapse_channel.core.at_rest_escrow import split_key_file

    try:
        written = split_key_file(
            args.key,
            threshold=args.threshold,
            share_count=args.shares,
            out_dir=args.out_dir,
        )
    except (ValueError, OSError) as exc:
        print(f"synapse encrypt-key escrow-split: {exc}")
        return 2
    print(f"wrote {len(written)} escrow shares under {args.out_dir}")
    for path in written:
        print(f"  {path}")
    return 0


def _cmd_escrow_recover(args: argparse.Namespace) -> int:
    """Recover a raw at-rest key file from threshold escrow shares."""
    from synapse_channel.core.at_rest_escrow import recover_key_file

    try:
        written = recover_key_file(args.share, out_path=args.out)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, OSError) as exc:
        print(f"synapse encrypt-key escrow-recover: {exc}")
        return 2
    print(f"recovered at-rest key (owner-only, 32 bytes): {written}")
    return 0


def add_escrow_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``escrow-split`` and ``escrow-recover`` subcommands."""
    escrow_split = nested.add_parser(
        "escrow-split",
        help="Split a raw at-rest key into threshold (Shamir) escrow shares for recovery.",
    )
    escrow_split.add_argument("--key", required=True, help="Owner-only raw 32-byte key file.")
    escrow_split.add_argument(
        "--threshold",
        type=int,
        required=True,
        help="Minimum number of shares required to recover the key (at least 2).",
    )
    escrow_split.add_argument(
        "--shares",
        type=int,
        required=True,
        help="Total number of shares to issue (>= threshold, <= 255).",
    )
    escrow_split.add_argument(
        "--out-dir",
        required=True,
        help="Directory for share-NN.json files (created if needed).",
    )
    escrow_split.set_defaults(func=_cmd_escrow_split)

    escrow_recover = nested.add_parser(
        "escrow-recover",
        help="Recover a raw at-rest key from threshold escrow shares.",
    )
    escrow_recover.add_argument(
        "--share",
        action="append",
        required=True,
        help="Path to an escrow share file (repeat; supply at least threshold shares).",
    )
    escrow_recover.add_argument(
        "--out",
        required=True,
        help="Destination raw key file (must not already exist).",
    )
    escrow_recover.set_defaults(func=_cmd_escrow_recover)
