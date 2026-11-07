# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hybrid post-quantum countersignatures of AEF tree heads, from the CLI
"""``synapse aef pq-key`` and ``aef countersign``, and the countersignature check of ``verify``.

* ``pq-key new`` writes a new owner-only hybrid key (Ed25519 + ML-DSA-65) and a
  trust file naming its public key.
* ``countersign`` verifies a tree head with the log's AEF trust file and writes
  a countersignature of exactly that head.
* ``verify --countersignature FILE --countersign-trust FILE`` reports each
  countersignature's verdict against the ``--sth`` head it binds, and
  ``--require-countersignature`` fails unless every one is ``VALID`` and its
  head is ``STH_COMPLETE`` for the export.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from synapse_channel.core.aef_countersign import (
    AefCountersignError,
    CountersignReport,
    CountersignVerdict,
    countersign_sth,
    sth_digest,
    verify_countersignature,
)
from synapse_channel.core.aef_sth import AefSthReport, AefSthVerdict
from synapse_channel.core.aef_trust_file import AefTrustFileError, load_aef_trust
from synapse_channel.core.owner_only_output import write_new_owner_only_text
from synapse_channel.core.secret_files import SecretFileError
from synapse_channel.core.signature_suite import (
    SUITE_HYBRID,
    SignatureSuiteError,
    generate_key,
    key_document,
    load_key,
    load_trust,
    trust_document,
)


def _write(path: str, text: str) -> str | None:
    """Write a new owner-only file; return an error line, or ``None``."""
    try:
        write_new_owner_only_text(path, text)
    except OSError as exc:
        return f"error: cannot write {path}: {exc.strerror}"
    return None


def _cmd_pq_key_new(args: argparse.Namespace) -> int:
    try:
        key = generate_key(SUITE_HYBRID)
    except SignatureSuiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    trust = trust_document(key, log_ids=tuple(args.log_id))
    for path, text in ((args.out, key_document(key)), (args.trust_out, trust)):
        if (problem := _write(path, text)) is not None:
            print(problem, file=sys.stderr)
            return 2
    print(
        f"wrote {SUITE_HYBRID} key {key.key_id()} to {args.out} "
        f"and its trust file to {args.trust_out}"
    )
    return 0


MAX_DOCUMENT_BYTES = 1 << 20
"""The largest tree head or countersignature file read, far above either's real size."""


def _read_object(path: str) -> dict[str, Any]:
    """Read one bounded JSON object; refuse oversized, malformed or deeply nested input."""
    with open(path, "rb") as handle:
        raw = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{path} exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError(f"{path} is not UTF-8 JSON") from None
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a JSON object")
    return document


def _cmd_countersign(args: argparse.Namespace) -> int:
    try:
        sth = _read_object(args.sth)
        document = countersign_sth(
            sth,
            trust_store=load_aef_trust(args.trust),
            key=load_key(args.key, suite=SUITE_HYBRID),
        )
    except (
        AefCountersignError,
        AefTrustFileError,
        SignatureSuiteError,
        SecretFileError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.out is None:
        sys.stdout.write(text)
        return 0
    if (problem := _write(args.out, text)) is not None:
        print(problem, file=sys.stderr)
        return 2
    print(
        f"countersigned the tree head of size {document['tree_size']} to {args.out}",
        file=sys.stderr,
    )
    return 0


def countersign_arguments_error(args: argparse.Namespace) -> str | None:
    """Return why the countersignature options of ``verify`` are unusable, or ``None``."""
    if args.require_countersignature and not args.countersignature:
        # Requiring a countersignature that was never given would pass vacuously.
        return "--require-countersignature needs at least one --countersignature"
    if args.countersignature and not args.countersign_trust:
        return "--countersignature needs --countersign-trust"
    if args.countersignature and not args.sth:
        return "--countersignature needs the --sth head it countersigns"
    return None


def check_countersignatures(
    args: argparse.Namespace,
    heads: Sequence[dict[str, Any]],
    sths: Sequence[AefSthReport],
) -> tuple[list[tuple[CountersignReport, str]], bool]:
    """Return each countersignature's report with its head's STH verdict, and the pass.

    A countersignature is matched to the ``--sth`` head whose digest it names;
    with none, it is ``MISMATCH``. The pass is true unless
    ``--require-countersignature`` is set and some countersignature is not
    ``VALID`` with an ``STH_COMPLETE`` head.

    Raises
    ------
    SignatureSuiteError, OSError, ValueError
        When a file cannot be read or parsed.
    """
    if not args.countersignature:
        return [], True
    trust = load_trust(args.countersign_trust)
    by_digest = {sth_digest(head): (head, report) for head, report in zip(heads, sths, strict=True)}
    results: list[tuple[CountersignReport, str]] = []
    for path in args.countersignature:
        document = _read_object(path)
        claimed = document.get("sth_sha256")
        head, sth_report = by_digest.get(
            claimed if isinstance(claimed, str) else "", (heads[0], None)
        )
        # A countersignature of no given head is checked against the first one,
        # whose digest it does not name, so an authentic one is MISMATCH.
        report = verify_countersignature(document, head, trust=trust)
        results.append((report, sth_report.verdict.value if sth_report is not None else "NO_HEAD"))
    passed = not args.require_countersignature or all(
        report.verdict is CountersignVerdict.VALID
        and sth_verdict == AefSthVerdict.STH_COMPLETE.value
        for report, sth_verdict in results
    )
    return results, passed


def countersign_json(results: Sequence[tuple[CountersignReport, str]]) -> list[dict[str, str]]:
    """Return the countersignature results for ``verify --json``."""
    return [
        {
            "verdict": report.verdict.value,
            "key_id": report.key_id,
            "sth_sha256": report.sth_sha256,
            "sth_verdict": sth_verdict,
        }
        for report, sth_verdict in results
    ]


def countersign_lines(results: Sequence[tuple[CountersignReport, str]]) -> list[str]:
    """Return one human-readable line per countersignature."""
    return [
        f"countersignature {report.verdict.value} key={report.key_id or '-'} "
        f"head={report.sth_sha256[:16] or '-'} sth={sth_verdict}"
        for report, sth_verdict in results
    ]


def add_verify_options(verify: argparse.ArgumentParser) -> None:
    """Add the countersignature options to ``aef verify``."""
    verify.add_argument(
        "--countersignature",
        action="append",
        default=[],
        metavar="FILE",
        help="Hybrid countersignature from 'synapse aef countersign' of one --sth head. "
        "Repeatable. Alone it does not change the exit code.",
    )
    verify.add_argument(
        "--countersign-trust",
        default=None,
        metavar="FILE",
        help="Trust file of the countersigning keys (from 'synapse aef pq-key new').",
    )
    verify.add_argument(
        "--require-countersignature",
        action="store_true",
        help="Also fail unless every countersignature is VALID and its head is STH_COMPLETE.",
    )


def add_parsers(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``aef pq-key new`` and ``aef countersign``."""
    pq_key = actions.add_parser("pq-key", help="Manage hybrid post-quantum countersigning keys.")
    pq_actions = pq_key.add_subparsers(dest="pq_key_command", required=True)
    new = pq_actions.add_parser("new", help="Write a new hybrid Ed25519 + ML-DSA-65 key.")
    new.add_argument("--out", required=True, help="New owner-only private key file.")
    new.add_argument("--trust-out", required=True, help="New trust file naming its public key.")
    new.add_argument(
        "--log-id",
        action="append",
        default=[],
        metavar="LOG_ID",
        help="Trust the key only for this AEF log. Repeatable; default: any log.",
    )
    new.set_defaults(func=_cmd_pq_key_new)
    countersign = actions.add_parser(
        "countersign", help="Countersign a verified AEF tree head with a hybrid key."
    )
    countersign.add_argument("--sth", required=True, help="Signed tree head from 'aef sth'.")
    countersign.add_argument("--trust", required=True, help="The log's AEF trust file.")
    countersign.add_argument("--key", required=True, help="Hybrid key from 'aef pq-key new'.")
    countersign.add_argument(
        "--out", default=None, help="Write to this new file instead of stdout."
    )
    countersign.set_defaults(func=_cmd_countersign)
