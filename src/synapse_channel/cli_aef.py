# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — offline AEF evidence verification CLI
"""``synapse aef``: verify a hub's AEF receipts offline, without the hub.

* ``trust`` writes the trust file for one hub's log from its public key.
* ``export`` copies a hub store's native receipts, in log order, to JSON Lines.
* ``verify`` checks receipts against a trust file under one clock. Receipts in
  one run share a replay index, so a repeated receipt is ``REPLAYED`` and a
  second receipt for a taken ``(log_id, seq)`` is ``CHAIN_CONFLICT``. It then
  checks each log's chain (:mod:`synapse_channel.core.aef_chain`);
  ``--require-chain`` also fails a broken chain.
* ``inclusion`` checks one receipt against a signed tree head and audit path.
* ``sth`` signs a tree head over a hub store's whole log with the log's key;
  ``verify --sth`` then tells whether an export is complete up to it
  (:mod:`synapse_channel.core.aef_sth`), and ``--require-sth`` fails otherwise.

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

from synapse_channel.cli_capability_cards import _write_new
from synapse_channel.core.aef_chain import AefChainReport, verify_aef_chain
from synapse_channel.core.aef_emission import AefEmissionError
from synapse_channel.core.aef_sth import AefSthReport, AefSthVerdict, sign_aef_sth, verify_aef_sth
from synapse_channel.core.aef_trust_file import (
    AefTrustFileError,
    aef_trust_document,
    load_aef_trust,
)
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefInclusionVerdict,
    AefReceiptIndex,
    AefTrustStore,
    AefVerification,
    verify_aef_inclusion,
    verify_aef_receipt,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence_sqlcipher import connect_event_store
from synapse_channel.core.receipt_signing import (
    ReceiptSigningError,
    load_receipt_signing_key,
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


def _stored_receipts(db: str, key_file: str | None) -> list[bytes]:
    """Read a hub store's canonical native receipts in log order, read-only."""
    if not Path(db).is_file():
        raise AefInputError(f"no hub store at {db}")
    try:
        connection, _encrypted = connect_event_store(db, key_file=key_file)
    except (OSError, ValueError, RuntimeError) as exc:
        raise AefInputError(f"cannot open {db}: {exc}") from None
    try:
        rows = connection.execute(
            "SELECT canonical_receipt FROM aef_receipts ORDER BY seq"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise AefInputError(
            f"{db} has no readable AEF receipts ({exc}); the hub needs "
            "--aef-signing-key, and an encrypted store needs --db-key-file"
        ) from None
    finally:
        connection.close()
    return [bytes(row[0]) for row in rows]


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        rows = _stored_receipts(args.db, args.db_key_file)
    except AefInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    lines = b"".join(row + b"\n" for row in rows)
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


def _cmd_sth(args: argparse.Namespace) -> int:
    try:
        rows = _stored_receipts(args.db, args.db_key_file)
        receipts = [_json_object(row, f"{args.db} receipt") for row in rows]
        head = sign_aef_sth(
            receipts,
            hub_id=args.hub_id,
            signing_key=load_receipt_signing_key(args.signing_key),
        )
    except (AefInputError, AefEmissionError, ReceiptSigningError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(head, indent=2, sort_keys=True) + "\n"
    if args.out is None:
        sys.stdout.write(text)
        return 0
    try:
        _write_new(args.out, text)
    except OSError as exc:
        print(f"error: cannot write {args.out}: {exc.strerror}", file=sys.stderr)
        return 2
    print(f"signed AEF tree head over {head['tree_size']} receipts to {args.out}", file=sys.stderr)
    return 0


def _verify_inputs(
    args: argparse.Namespace, now_ms: int
) -> tuple[AefTrustStore, list[dict[str, Any]], list[tuple[dict[str, Any], AefVerification]]]:
    """Load the trust file and tree heads, and verify each receipt in one replay index."""
    index = AefReceiptIndex()
    trust = load_aef_trust(args.trust)
    heads = [_json_object(_read_bounded(path), path) for path in args.sth]
    checked: list[tuple[dict[str, Any], AefVerification]] = []
    for receipt in _receipts(args.receipts):
        outcome = verify_aef_receipt(receipt, trust_store=trust, now_ms=now_ms, seen=index)
        checked.append((receipt, outcome))
    return trust, heads, checked


def _print_verify(
    args: argparse.Namespace,
    now_ms: int,
    checked: list[tuple[dict[str, Any], AefVerification]],
    chains: list[AefChainReport],
    sths: list[AefSthReport],
) -> None:
    """Print the verify result as JSON or as one line per receipt, chain and head."""
    results = [(receipt.get("seq"), outcome) for receipt, outcome in checked]
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
        document = {
            "now_ms": now_ms,
            "valid": valid,
            "receipts": rendered,
            "chains": [_chain_json(chain) for chain in chains],
            "sths": [_sth_json(report) for report in sths],
        }
        print(json.dumps(document, sort_keys=True))
        return
    for seq, outcome in results:
        reasons = ", ".join(outcome.reasons)
        print(f"seq={seq} {outcome.verdict.value} {outcome.receipt_id} ({reasons})")
    print(f"{valid} of {len(results)} receipts VALID at now_ms={now_ms}")
    for chain in chains:
        print(_chain_line(chain))
    for report in sths:
        print(_sth_line(report))


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.require_sth and not args.sth:
        # Requiring completeness against no tree head would pass vacuously.
        print("error: --require-sth needs at least one --sth", file=sys.stderr)
        return 2
    now_ms = int(time.time() * 1000) if args.now_ms is None else args.now_ms
    try:
        trust, heads, checked = _verify_inputs(args, now_ms)
    except (AefTrustFileError, AefInputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    chains = verify_aef_chain(checked)
    exported = [receipt for receipt, _outcome in checked]
    sths = [verify_aef_sth(exported, head, trust_store=trust) for head in heads]
    _print_verify(args, now_ms, checked, chains, sths)
    valid = all(outcome.verdict is AefVerdictCode.VALID for _receipt, outcome in checked)
    chained = not args.require_chain or all(chain.continuous for chain in chains)
    complete = not args.require_sth or all(
        report.verdict is AefSthVerdict.STH_COMPLETE for report in sths
    )
    return 0 if checked and valid and chained and complete else 1


def _sth_json(report: AefSthReport) -> dict[str, Any]:
    """Render one tree head's verdict as JSON."""
    return {
        "log_id": report.log_id,
        "tree_size": report.tree_size,
        "verdict": report.verdict.value,
        "detail": report.detail,
    }


def _sth_line(report: AefSthReport) -> str:
    """Render one tree head's verdict as one line."""
    log = report.log_id[:16] if report.log_id else "?"
    return f"sth log={log} size={report.tree_size}: {report.verdict.value} ({report.detail})"


def _chain_json(chain: AefChainReport) -> dict[str, Any]:
    """Render one log's chain report as JSON."""
    return {
        "log_id": chain.log_id,
        "first_seq": chain.first_seq,
        "last_seq": chain.last_seq,
        "receipts": chain.receipts,
        "verdict": chain.verdict.value,
        "issues": [
            {"code": issue.code.value, "seq": issue.seq, "detail": issue.detail}
            for issue in chain.issues
        ],
    }


def _chain_line(chain: AefChainReport) -> str:
    """Render one log's chain report as one line."""
    issues = "; ".join(f"{issue.code.value} at seq {issue.seq}" for issue in chain.issues)
    suffix = f" ({issues})" if issues else ""
    return (
        f"chain log={chain.log_id[:16]} seq {chain.first_seq}..{chain.last_seq}: "
        f"{chain.verdict.value}{suffix}"
    )


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

    _add_verify_parser(actions)
    _add_sth_parser(actions)

    inclusion = actions.add_parser(
        "inclusion", help="Verify one receipt against a signed tree head and audit path."
    )
    inclusion.add_argument("--trust", required=True, help="AEF trust file (aef-trust-v0.1).")
    inclusion.add_argument("--receipt", required=True, help="The receipt JSON document.")
    inclusion.add_argument("--sth", required=True, help="The signed tree head JSON document.")
    inclusion.add_argument("--proof", required=True, help="The inclusion proof JSON document.")
    inclusion.set_defaults(func=_cmd_inclusion)


def _add_verify_parser(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``aef verify``."""
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
    verify.add_argument(
        "--require-chain",
        action="store_true",
        help="Also fail unless each log's authentic receipts form one unbroken chain "
        "(no gap, broken link, fork, duplicate or wrong genesis).",
    )
    verify.add_argument(
        "--sth",
        action="append",
        default=[],
        metavar="FILE",
        help="Signed tree head from 'synapse aef sth'; report whether the receipts are "
        "complete up to it. Repeatable. Alone it does not change the exit code.",
    )
    verify.add_argument(
        "--require-sth",
        action="store_true",
        help="Also fail unless every --sth verdict is STH_COMPLETE.",
    )
    verify.set_defaults(func=_cmd_verify)


def _add_sth_parser(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``aef sth``."""
    sth = actions.add_parser(
        "sth", help="Sign a tree head over a hub store's whole AEF log with the log's key."
    )
    sth.add_argument("db", help="Path to the hub event store (the hub's --db).")
    sth.add_argument("--hub-id", required=True, help="The hub's --hub-id.")
    sth.add_argument(
        "--signing-key", required=True, help="The hub's --aef-signing-key (private PEM)."
    )
    sth.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted store."
    )
    sth.add_argument("--out", default=None, help="Write to this new file instead of stdout.")
    sth.set_defaults(func=_cmd_sth)
