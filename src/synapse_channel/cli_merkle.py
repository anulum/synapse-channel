# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Merkle-commitment CLI commands
"""CLI wrappers for the event-log Merkle commitment.

``merkle root`` commits the durable log to one fingerprint (optionally gated
against an expected root); ``merkle prove`` emits an ``O(log n)`` inclusion proof
for one event; ``merkle verify`` checks such a proof offline, with no event store
— the light-client verification a follower runs against a trusted root; ``merkle
keygen`` creates the hub deployment's receipt-signing keypair, whose private half
signs receipt commitments (``verify-release --signing-key``) and whose public
half verifies them (``policy-check --trusted-signing-key``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from synapse_channel.core.merkle import (
    proof_from_json,
    proof_to_json,
    render_proof_markdown,
    render_root_markdown,
    root_to_json,
    run_proof,
    run_root,
    verify_inclusion,
    verify_root,
)
from synapse_channel.core.receipt_signing import (
    ReceiptSigningError,
    generate_receipt_signing_key,
)


def _cmd_root(args: argparse.Namespace) -> int:
    """Commit the event log to a Merkle root and optionally gate it."""
    try:
        root = run_root(
            args.db,
            through_seq=args.through,
            key_file=getattr(args, "db_key_file", None),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(root_to_json(root), indent=2, sort_keys=True))
    else:
        print(render_root_markdown(root))
    if args.expect:
        if verify_root(root.root, args.expect):
            print(f"root matches: {root.root}", file=sys.stderr)
            return 0
        print(
            f"root mismatch: expected {args.expect.strip().lower()}, got {root.root}",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_prove(args: argparse.Namespace) -> int:
    """Emit an inclusion proof for one event's sequence."""
    try:
        proof = run_proof(
            args.db,
            args.seq,
            through_seq=args.through,
            key_file=getattr(args, "db_key_file", None),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if proof is None:
        print(f"no event at seq {args.seq} in the committed log", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(proof_to_json(proof), indent=2, sort_keys=True))
    else:
        print(render_proof_markdown(proof))
    return 0


def _emit_verify_verdict(*, valid: bool, seq: int, root: str, reason: str) -> None:
    """Print a machine-readable verify verdict to stdout, as ``root``/``prove`` do."""
    verdict: dict[str, object] = {"valid": valid, "seq": seq, "root": root}
    if reason:
        verdict["reason"] = reason
    print(json.dumps(verdict, indent=2, sort_keys=True))


def _cmd_verify(args: argparse.Namespace) -> int:
    """Verify an inclusion proof offline against its own and an expected root.

    The exit code is the machine signal (0 valid / 1 bad-proof|mismatch /
    2 unreadable-file). Without ``--json`` the human confirmation and gate
    diagnostics go to stderr, matching ``root --expect``'s ``root matches`` line;
    with ``--json`` a structured verdict is emitted to stdout, giving ``verify``
    the same stdout payload that ``root`` and ``prove`` already carry.
    """
    path = Path(args.proof)
    if not path.exists():
        print(f"missing proof file: {path}", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        proof = proof_from_json(data)
    except (ValueError, OSError) as exc:
        print(f"unreadable proof: {exc}", file=sys.stderr)
        return 2
    if not verify_inclusion(proof):
        reason = f"proof does not reconstruct its root {proof.root}"
        if args.json:
            _emit_verify_verdict(valid=False, seq=proof.seq, root=proof.root, reason=reason)
        else:
            print(reason, file=sys.stderr)
        return 1
    if args.expect and not verify_root(proof.root, args.expect):
        reason = f"root mismatch: expected {args.expect.strip().lower()}, got {proof.root}"
        if args.json:
            _emit_verify_verdict(valid=False, seq=proof.seq, root=proof.root, reason=reason)
        else:
            print(reason, file=sys.stderr)
        return 1
    if args.json:
        _emit_verify_verdict(valid=True, seq=proof.seq, root=proof.root, reason="")
    else:
        print(
            f"proof valid: seq {proof.seq} is in the log under root {proof.root}",
            file=sys.stderr,
        )
    return 0


def _cmd_keygen(args: argparse.Namespace) -> int:
    """Generate the hub deployment's receipt-signing keypair."""
    try:
        generated = generate_receipt_signing_key(args.path)
    except ReceiptSigningError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"receipt-signing key: {args.path} (0600, keep private)")
    print(f"verification key:    {args.path}.pub (distribute to verifiers)")
    print(f"key_id:              {generated.key_id}")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``merkle`` subcommand and its actions."""
    merkle = subparsers.add_parser(
        "merkle",
        help="Commit the event log to a Merkle root and prove event inclusion.",
    )
    actions = merkle.add_subparsers(dest="merkle_command", required=True)

    root = actions.add_parser("root", help="Commit the log to a Merkle root.")
    root.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    root.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    root.add_argument(
        "--through",
        type=int,
        default=None,
        metavar="SEQ",
        help="Commit only events up to and including this sequence.",
    )
    root.add_argument(
        "--expect",
        default="",
        metavar="ROOT",
        help="Gate on an expected root; exit 1 on mismatch.",
    )
    root.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    root.set_defaults(func=_cmd_root)

    prove = actions.add_parser("prove", help="Emit an inclusion proof for one event.")
    prove.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    prove.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    prove.add_argument("seq", type=int, metavar="SEQ", help="Event sequence to prove.")
    prove.add_argument(
        "--through",
        type=int,
        default=None,
        metavar="SEQ",
        help="Prove against the tree of events up to this sequence.",
    )
    prove.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    prove.set_defaults(func=_cmd_prove)

    verify = actions.add_parser("verify", help="Verify an inclusion proof offline (no store).")
    verify.add_argument("proof", help="Path to a proof JSON file from 'merkle prove --json'.")
    verify.add_argument(
        "--expect",
        default="",
        metavar="ROOT",
        help="Also require the proof's root to equal this trusted root.",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable verdict to stdout instead of a stderr line.",
    )
    verify.set_defaults(func=_cmd_verify)

    keygen = actions.add_parser(
        "keygen",
        help="Generate the receipt-signing keypair that attests receipt commitments.",
    )
    keygen.add_argument(
        "path",
        help="Private-key file to create (0600); the public half goes to PATH.pub.",
    )
    keygen.set_defaults(func=_cmd_keygen)
