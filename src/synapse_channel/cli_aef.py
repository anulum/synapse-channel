# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — offline AEF evidence verification CLI (K4-AEF-SURFACE)
"""``synapse aef``: verify a hub's AEF receipts offline, without the hub.

* ``trust`` writes the trust file for one hub's log from its public key.
* ``export`` copies a hub store's native receipts, in log order, to JSON Lines.
* ``verify`` checks receipts against a trust file under one clock. Receipts in
  one run share a replay index, so a repeated receipt is ``REPLAYED`` and a
  second receipt for a taken ``(log_id, seq)`` is ``CHAIN_CONFLICT``.
* ``inclusion`` checks one receipt against a signed tree head and audit path.

Exit codes: ``0`` when every verdict is valid, ``1`` when any is not, ``2``
when an input cannot be read or parsed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from synapse_channel.core.aef_trust_file import (
    AefTrustFileError,
    aef_trust_document,
    load_aef_trust,
)
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefInclusionVerdict,
    AefReceiptIndex,
    AefVerification,
    verify_aef_inclusion,
    verify_aef_receipt,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence_sqlcipher import connect_event_store
from synapse_channel.core.receipt_signing import (
    ReceiptSigningError,
    load_receipt_verification_key,
)

_MAX_DOCUMENT_BYTES = 64 << 20


class AefInputError(SynapseError, ValueError):
    """An ``aef`` input file cannot be read or parsed."""

    code = "aef_input"


def _read_bounded(path: str, *, limit: int = _MAX_DOCUMENT_BYTES) -> bytes:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise AefInputError(f"cannot read {path}: {exc.strerror}") from None
    if len(raw) > limit:
        raise AefInputError(f"{path} exceeds {limit} bytes")
    return raw


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AefInputError(f"{label} is not UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise AefInputError(f"{label} must be a JSON object")
    return value


def _receipts(path: str) -> Iterator[dict[str, Any]]:
    """Yield receipts from a ``.jsonl`` file, or a JSON object or array."""
    raw = _read_bounded(path)
    if path.endswith(".jsonl"):
        for number, line in enumerate(raw.splitlines(), start=1):
            if line.strip():
                yield _json_object(line, f"{path} line {number}")
        return
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AefInputError(f"{path} is not UTF-8 JSON") from None
    items = document if isinstance(document, list) else [document]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AefInputError(f"{path} item {index} must be a JSON object")
        yield item


def _cmd_trust(args: argparse.Namespace) -> int:
    try:
        key = load_receipt_verification_key(args.public_key)
    except ReceiptSigningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(
        aef_trust_document(hub_id=args.hub_id, public_key=key.public_key), indent=2, sort_keys=True
    )
    if args.out is None:
        print(text)
        return 0
    try:
        with Path(args.out).open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError as exc:
        print(f"error: cannot write {args.out}: {exc.strerror}", file=sys.stderr)
        return 2
    print(f"wrote AEF trust for hub {args.hub_id!r} to {args.out}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    if not Path(args.db).is_file():
        print(f"error: no hub store at {args.db}", file=sys.stderr)
        return 2
    try:
        connection, _encrypted = connect_event_store(args.db, key_file=args.db_key_file)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: cannot open {args.db}: {exc}", file=sys.stderr)
        return 2
    try:
        rows = connection.execute(
            "SELECT canonical_receipt FROM aef_receipts ORDER BY seq"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        print(
            f"error: {args.db} has no readable AEF receipts ({exc}); the hub needs "
            "--aef-signing-key, and an encrypted store needs --db-key-file",
            file=sys.stderr,
        )
        return 2
    finally:
        connection.close()
    lines = b"".join(bytes(row[0]) + b"\n" for row in rows)
    if args.out is None:
        sys.stdout.write(lines.decode("utf-8"))
    else:
        try:
            with Path(args.out).open("xb") as handle:
                handle.write(lines)
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc.strerror}", file=sys.stderr)
            return 2
        print(f"exported {len(rows)} AEF receipts to {args.out}", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    now_ms = int(time.time() * 1000) if args.now_ms is None else args.now_ms
    index = AefReceiptIndex()
    results: list[tuple[object, AefVerification]] = []
    try:
        trust = load_aef_trust(args.trust)
        for receipt in _receipts(args.receipts):
            outcome = verify_aef_receipt(receipt, trust_store=trust, now_ms=now_ms, seen=index)
            results.append((receipt.get("seq"), outcome))
    except (AefTrustFileError, AefInputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    valid = sum(1 for _seq, outcome in results if outcome.verdict is AefVerdictCode.VALID)
    if args.json:
        rendered = [
            {
                "seq": seq,
                "receipt_id": outcome.receipt_id,
                "key_id": outcome.key_id,
                "verdict": outcome.verdict.value,
                "reasons": list(outcome.reasons),
            }
            for seq, outcome in results
        ]
        print(json.dumps({"now_ms": now_ms, "valid": valid, "receipts": rendered}, sort_keys=True))
    else:
        for seq, outcome in results:
            reasons = ", ".join(outcome.reasons)
            print(f"seq={seq} {outcome.verdict.value} {outcome.receipt_id} ({reasons})")
        print(f"{valid} of {len(results)} receipts VALID at now_ms={now_ms}")
    return 0 if results and valid == len(results) else 1


def _cmd_inclusion(args: argparse.Namespace) -> int:
    try:
        trust = load_aef_trust(args.trust)
        receipt = _json_object(_read_bounded(args.receipt), args.receipt)
        sth = _json_object(_read_bounded(args.sth), args.sth)
        proof = _json_object(_read_bounded(args.proof), args.proof)
    except (AefTrustFileError, AefInputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    verdict = verify_aef_inclusion(receipt, sth, proof, trust_store=trust)
    print(verdict.value)
    return 0 if verdict is AefInclusionVerdict.INCLUSION_VALID else 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``aef`` subcommand and its actions."""
    aef = subparsers.add_parser(
        "aef",
        help="Verify a hub's AEF evidence receipts offline, without the hub.",
    )
    actions = aef.add_subparsers(dest="aef_command", required=True)

    trust = actions.add_parser("trust", help="Write the trust file for one hub's AEF log.")
    trust.add_argument(
        "--public-key",
        required=True,
        help="The .pub file of the hub's --aef-signing-key (from 'synapse merkle keygen').",
    )
    trust.add_argument("--hub-id", required=True, help="The hub's --hub-id.")
    trust.add_argument("--out", default=None, help="Write to this new file instead of stdout.")
    trust.set_defaults(func=_cmd_trust)

    export = actions.add_parser(
        "export", help="Copy a hub store's native AEF receipts to JSON Lines, in log order."
    )
    export.add_argument("db", help="Path to the hub event store (the hub's --db).")
    export.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted store."
    )
    export.add_argument("--out", default=None, help="Write to this new file instead of stdout.")
    export.set_defaults(func=_cmd_export)

    verify = actions.add_parser(
        "verify", help="Verify receipts against a trust file; detect replays and conflicts."
    )
    verify.add_argument(
        "receipts", help="Receipts: a .jsonl file, or a JSON object or array of objects."
    )
    verify.add_argument("--trust", required=True, help="AEF trust file (aef-trust-v0.1).")
    verify.add_argument(
        "--now-ms",
        type=int,
        default=None,
        metavar="MS",
        help="Verifier clock in epoch milliseconds for expiry (default: now).",
    )
    verify.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    verify.set_defaults(func=_cmd_verify)

    inclusion = actions.add_parser(
        "inclusion", help="Verify one receipt against a signed tree head and audit path."
    )
    inclusion.add_argument("--trust", required=True, help="AEF trust file (aef-trust-v0.1).")
    inclusion.add_argument("--receipt", required=True, help="The receipt JSON document.")
    inclusion.add_argument("--sth", required=True, help="The signed tree head JSON document.")
    inclusion.add_argument("--proof", required=True, help="The inclusion proof JSON document.")
    inclusion.set_defaults(func=_cmd_inclusion)
