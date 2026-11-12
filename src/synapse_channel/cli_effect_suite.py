# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse effect-suite run` and `verify`
"""``synapse effect-suite``: run the effect-safety suite and verify its records offline.

``run`` starts a disposable hub and Git repository for each variant of cases
1 to 4, drives this package's own stack through them, and writes one signed
record per variant to ``--output``. It prints the failed and unknown variants
first and no score. It needs ``git`` on ``PATH`` and a private Ed25519 key file
(``synapse merkle keygen KEY`` writes one).

``verify`` checks records against the public key file, without a hub.

Exit codes: 0 every variant passed or is not applicable (``run``), or every
record verifies (``verify``); 1 a variant failed or is unknown, or a record does
not verify; 2 an input could not be used (no ``git``, an unusable key, an
unknown variant name, an unreadable record) or the ``cryptography`` package of
the encryption extra is not installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from synapse_channel.core.receipt_signing import ReceiptSigningError, load_receipt_verification_key
from synapse_channel.effect_suite.cases import VARIANTS
from synapse_channel.effect_suite.record import (
    EffectSuiteKeyError,
    load_signing_key,
    require_cryptography,
    verification_refusal,
)
from synapse_channel.effect_suite.runner import run_suite
from synapse_channel.git.git_runtime import GitError, git_executable

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2

_ORDER = ("fail", "unknown", "pass", "not_applicable")
"""Verdicts in the order the run prints them: failures and unknowns first."""


def cmd_run(args: argparse.Namespace) -> int:
    """Dispatch ``synapse effect-suite run``."""
    known = {variant.name for variant in VARIANTS}
    unknown = sorted(set(args.variant) - known)
    if unknown:
        print(f"effect suite refused: no variant is called {', '.join(unknown)}")
        return EXIT_UNUSABLE
    try:
        git = git_executable()
        key = load_signing_key(args.signing_key)
    except (GitError, EffectSuiteKeyError) as exc:
        print(f"effect suite refused: {exc}")
        return EXIT_UNUSABLE
    results = asyncio.run(run_suite(args.output, key, git=git, names=tuple(args.variant)))
    for outcome, path in sorted(results, key=lambda item: _ORDER.index(item[0].verdict)):
        print(f"{outcome.verdict} {outcome.variant.name}: {outcome.reason} ({path.name})")
    print(f"{len(results)} records written to {args.output}, signed by key {key.key_id}")
    bad = [outcome for outcome, _ in results if outcome.verdict in ("fail", "unknown")]
    return EXIT_FAILED if bad else EXIT_PASSED


def cmd_verify(args: argparse.Namespace) -> int:
    """Dispatch ``synapse effect-suite verify``."""
    try:
        require_cryptography()
        key = load_receipt_verification_key(args.public_key)
    except (EffectSuiteKeyError, ReceiptSigningError) as exc:
        print(f"effect suite verify refused: {exc}")
        return EXIT_UNUSABLE
    invalid = 0
    for path in args.records:
        try:
            record = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"effect suite verify refused: cannot read {path}: {type(exc).__name__}")
            return EXIT_UNUSABLE
        refusal = (
            verification_refusal(record, key) if isinstance(record, dict) else "not a JSON object"
        )
        if refusal is None:
            print(f"valid {path}")
        else:
            invalid += 1
            print(f"invalid {path}: {refusal}")
    return EXIT_FAILED if invalid else EXIT_PASSED


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse effect-suite run`` and ``verify``."""
    group = subparsers.add_parser(
        "effect-suite",
        help="Run the effect-safety suite against this stack, or verify its records.",
    )
    actions = group.add_subparsers(dest="effect_suite_command", required=True)
    run = actions.add_parser(
        "run", help="Run cases 1 to 4 and write one signed record per variant."
    )
    run.add_argument(
        "--signing-key", type=Path, required=True, help="Owner-only Ed25519 private key file."
    )
    run.add_argument("--output", type=Path, required=True, help="Folder for the records.")
    run.add_argument(
        "--variant",
        action="append",
        default=[],
        help="Run only this variant (repeatable), for example 3d.",
    )
    run.set_defaults(func=cmd_run)
    verify = actions.add_parser("verify", help="Verify records offline with the public key file.")
    verify.add_argument("--public-key", type=Path, required=True, help="The key file's .pub half.")
    verify.add_argument("records", nargs="+", type=Path, help="Record files to check.")
    verify.set_defaults(func=cmd_verify)
