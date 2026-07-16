# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deliberation conclude/verify CLI
"""``synapse deliberate`` — conclude a council into a sealed export package.

``deliberate conclude`` reads a deliberation spec (JSON), builds the validated
:class:`~synapse_channel.core.deliberation.ExportPackage`, and — when a receipt
key is supplied — seals it into a verifiable G7 receipt, then writes the document
to an owner-only output file (never replacing an existing one). ``deliberate
verify`` checks a sealed package against a receipt trust key.

The output is written ``0600`` (owner-only): an export package may carry
pre-redaction deliberation content, so it fails safe to the author until redaction
(G1b) and an explicit sharing step widen it. This is the first consumer that wires
:mod:`synapse_channel.core.deliberation` into the CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from synapse_channel.core.deliberation import (
    DeliberationError,
    export_package_from_mapping,
    seal_export_package,
    verify_sealed_package,
)
from synapse_channel.core.receipt_signing import (
    ReceiptSigningError,
    load_receipt_signing_key,
    load_receipt_verification_key,
)


def _write_new(path: str | Path, text: str) -> None:
    """Create an owner-only output file and refuse to replace an existing one."""
    file = Path(path).expanduser()
    file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        file.unlink(missing_ok=True)
        raise


def _load_spec(path: str) -> dict[str, object] | None:
    """Load and JSON-parse a deliberation spec, printing on failure."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read deliberation spec {path}: {exc}", file=sys.stderr)
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print(f"deliberation spec {path} is not valid JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"deliberation spec {path} must be a JSON object", file=sys.stderr)
        return None
    return data


def _cmd_conclude(args: argparse.Namespace) -> int:
    """Build (and optionally seal) an export package from a spec and write it."""
    spec = _load_spec(args.spec)
    if spec is None:
        return 2
    try:
        package = export_package_from_mapping(spec)
    except DeliberationError as exc:
        print(f"invalid deliberation spec: {exc}", file=sys.stderr)
        return 2

    if args.receipt_key:
        try:
            key = load_receipt_signing_key(args.receipt_key)
        except ReceiptSigningError as exc:
            print(f"cannot load receipt-signing key: {exc}", file=sys.stderr)
            return 2
        document: dict[str, object] = seal_export_package(package, key=key)
        posture = "sealed"
    else:
        document = package.canonical_content()
        posture = "unsealed"

    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        _write_new(args.out, text)
    except OSError as exc:
        print(f"cannot write {args.out}: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {posture} export package to {args.out}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Verify a sealed export package against a receipt trust key."""
    try:
        raw = Path(args.sealed).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read sealed package {args.sealed}: {exc}", file=sys.stderr)
        return 2
    try:
        sealed = json.loads(raw)
    except ValueError as exc:
        print(f"sealed package {args.sealed} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(sealed, dict):
        print(f"sealed package {args.sealed} must be a JSON object", file=sys.stderr)
        return 2
    try:
        verkey = load_receipt_verification_key(args.trust)
    except ReceiptSigningError as exc:
        print(f"cannot load receipt trust key: {exc}", file=sys.stderr)
        return 2

    outcome = verify_sealed_package(sealed, trusted_keys={verkey.key_id: verkey.public_key})
    if outcome.ok:
        print(f"OK: sealed package verified (key {outcome.signature.key_id})")
        return 0
    print(f"FAIL: {outcome.reason}", file=sys.stderr)
    return 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``deliberate`` conclude/verify commands."""
    group = subparsers.add_parser(
        "deliberate",
        help="Conclude a council into a sealed, verifiable export package.",
    )
    nested = group.add_subparsers(dest="deliberate_command", required=True)

    conclude = nested.add_parser(
        "conclude", help="Build (and optionally seal) an export package from a spec."
    )
    conclude.add_argument(
        "--from", dest="spec", required=True, metavar="SPEC.json", help="Deliberation spec JSON."
    )
    conclude.add_argument(
        "--receipt-key",
        default="",
        metavar="PRIVATE.pem",
        help="Receipt-signing key; when given, the package is sealed as a G7 receipt.",
    )
    conclude.add_argument(
        "--out", required=True, metavar="FILE", help="Create this file; never replace."
    )
    conclude.set_defaults(func=_cmd_conclude)

    verify = nested.add_parser("verify", help="Verify a sealed export package.")
    verify.add_argument("sealed", metavar="SEALED.json")
    verify.add_argument(
        "--trust", required=True, metavar="VERKEY.pub", help="Receipt verification key document."
    )
    verify.set_defaults(func=_cmd_verify)
