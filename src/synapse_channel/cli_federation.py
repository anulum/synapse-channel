# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse federation` CLI: exchange, import, list, and revoke peer domains
"""``synapse federation`` — exchange, import, list, and revoke operator-confirmed peer domains.

Trust between Synapse domains is established out-of-band: an operator receives a peer
domain's bundle through a trusted channel and imports it explicitly. ``import`` reads
that bundle file, requires a ``--confirmed-by`` operator, records the provenance, and
adds the peering to the local store; ``list`` shows the imported peerings, their
provenance, and each peering's age — trust material is a lifecycle, so an expired
bundle shows as such and ``--max-age`` flags peerings whose ceremony has gone stale;
``revoke`` marks a peering revoked so it fails authorisation while keeping
its audit record. There is no auto-discovery and no trust-on-first-use — every peering
is auditable back to a human decision.

The exchange pair moves the bundle *transport* onto the wire while keeping that trust
decision with the operator: ``offer`` validates this domain's own bundle material and
prints its fingerprints (served by ``synapse hub --federation-offer``), and ``fetch``
pulls a peer hub's offered material to a file and prints the same fingerprint block —
never importing. Both sides compare the bundle fingerprint out-of-band, the
SSH-known-hosts ceremony, and only then run the explicit ``import``.

The policy lives in :mod:`synapse_channel.core.federation`, the persistence in
:mod:`synapse_channel.core.federation_store`, and the exchange halves in
:mod:`synapse_channel.core.federation_wire` and
:mod:`synapse_channel.core.federation_fetch`; this is the thin I/O shell, with an
injectable clock, store path, and fetcher so the commands are testable without a real
home or network.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_fetch import (
    DEFAULT_FETCH_TIMEOUT,
    FederationFetchError,
    fetch_federation_offer,
)
from synapse_channel.core.federation_store import (
    FederationRecord,
    FederationStoreError,
    PeerProvenance,
    load_store,
    merge_record,
    peer_from_dict,
    save_store,
)
from synapse_channel.core.federation_wire import (
    FederationWireError,
    decode_federation_offer,
    encode_federation_offer,
    render_offer_fingerprints,
)

Clock = Callable[[], float]

DEFAULT_STORE = "~/.synapse/federation.json"
"""Default operator-level federation store path."""

SECONDS_PER_DAY = 86400.0
"""One day of peering age, in the store's epoch seconds."""


def _store_path(args: argparse.Namespace) -> Path:
    """Return the federation store path, expanding ``~`` and any override."""
    return Path(args.store).expanduser()


def _cmd_import(args: argparse.Namespace, *, clock: Clock = time.time) -> int:
    """Import an out-of-band peer-domain bundle with operator-confirmed provenance."""
    if args.max_age is not None and args.max_age <= 0:
        print("--max-age must be a positive number of days", file=sys.stderr)
        return 2
    try:
        raw = Path(args.bundle).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read bundle file: {args.bundle}", file=sys.stderr)
        del exc
        return 2
    try:
        peer = peer_from_dict(json.loads(raw))
        records = load_store(_store_path(args))
    except (json.JSONDecodeError, FederationStoreError) as exc:
        print(f"invalid federation bundle: {exc}", file=sys.stderr)
        return 2
    if args.max_age is not None:
        _warn_import_expiry(peer.expires_at, max_age_days=args.max_age, now=clock())
    record = FederationRecord(
        peer=peer,
        provenance=PeerProvenance(
            source=args.source or Path(args.bundle).name,
            imported_at=clock(),
            confirmed_by=args.confirmed_by,
        ),
    )
    existed = peer.domain_id in records
    save_store(_store_path(args), merge_record(records, record).values())
    verb = "updated" if existed else "imported"
    print(
        f"{verb} peering with domain '{peer.domain_id}' "
        f"({len(peer.namespaces)} namespaces, {len(peer.signing_key_ids)} keys, "
        f"{len(peer.certificate_pins)} pins), confirmed by {args.confirmed_by}"
    )
    return 0


def _cmd_list(args: argparse.Namespace, *, clock: Clock = time.time) -> int:
    """List imported peer domains, each peering's age, and any stale trust.

    Every line carries the peering's age since its confirmed import, and a
    peering whose bundle expiry has passed shows ``[expired]`` instead of
    ``[active]``. With ``--max-age DAYS``, an active peering imported
    longer ago than the threshold is flagged stale and the command exits
    ``1`` — trust material is a lifecycle, not a one-off ceremony.
    """
    if args.max_age is not None and args.max_age <= 0:
        print("--max-age must be a positive number of days", file=sys.stderr)
        return 2
    try:
        records = load_store(_store_path(args))
    except FederationStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not records:
        print("no peer domains imported")
        return 0
    now = clock()
    stale = 0
    print(f"{len(records)} peer domain(s):")
    for domain_id in sorted(records):
        record = records[domain_id]
        peer = record.peer
        if peer.revoked:
            state = "revoked"
        elif peer.expires_at is not None and now >= peer.expires_at:
            state = "expired"
        else:
            state = "active"
        age_days = max(0.0, now - record.provenance.imported_at) / SECONDS_PER_DAY
        marker = ""
        if args.max_age is not None and state == "active" and age_days > args.max_age:
            stale += 1
            marker = f" [stale: imported {age_days:.0f} days ago > {args.max_age:g}]"
        namespaces = ", ".join(sorted(peer.namespaces)) or "(none)"
        print(
            f"  {domain_id} [{state}]{marker} namespaces={namespaces} "
            f"keys={len(peer.signing_key_ids)} pins={len(peer.certificate_pins)} "
            f"scope={len(peer.scope_grants)} "
            f"— confirmed by {record.provenance.confirmed_by} from {record.provenance.source}, "
            f"imported {age_days:.0f} day(s) ago"
        )
    if stale:
        print(
            f"{stale} peering(s) exceed --max-age {args.max_age:g} days; "
            "re-run the exchange ceremony to refresh their trust material",
            file=sys.stderr,
        )
        return 1
    return 0


def _warn_import_expiry(expires_at: float | None, *, max_age_days: float, now: float) -> None:
    """Warn when an imported bundle's lifetime overruns the operator's policy.

    Advisory only: the operator is confirming the import explicitly, so the
    command still succeeds — but a bundle that never expires, or expires
    further out than ``--max-age`` days, is exactly the trust material the
    listing would later flag as stale, and the cheapest moment to say so is
    before it lands in the store.
    """
    if expires_at is None:
        print(
            f"warning: bundle never expires; --max-age {max_age_days:g} days asks for "
            "bounded trust — consider having the peer re-issue it with an expiry",
            file=sys.stderr,
        )
        return
    horizon = now + max_age_days * SECONDS_PER_DAY
    if expires_at > horizon:
        days_out = (expires_at - now) / SECONDS_PER_DAY
        print(
            f"warning: bundle expiry is {days_out:.0f} days out, beyond --max-age {max_age_days:g}",
            file=sys.stderr,
        )


def _cmd_revoke(args: argparse.Namespace) -> int:
    """Revoke a peering so it fails authorisation, keeping its audit record."""
    try:
        records = load_store(_store_path(args))
    except FederationStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    record = records.get(args.domain)
    if record is None:
        print(f"no peering with domain '{args.domain}'", file=sys.stderr)
        return 2
    revoked = dataclasses.replace(record, peer=dataclasses.replace(record.peer, revoked=True))
    save_store(_store_path(args), merge_record(records, revoked).values())
    print(f"revoked peering with domain '{args.domain}'")
    return 0


Fetcher = Callable[..., Coroutine[Any, Any, FederationPeer]]


def _cmd_offer(args: argparse.Namespace) -> int:
    """Validate this domain's own bundle material and print the ceremony fingerprints."""
    try:
        raw = Path(args.bundle).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read bundle file: {args.bundle}", file=sys.stderr)
        del exc
        return 2
    try:
        peer = decode_federation_offer(json.loads(raw))
    except (json.JSONDecodeError, FederationWireError) as exc:
        print(f"invalid federation bundle: {exc}", file=sys.stderr)
        return 2
    print(render_offer_fingerprints(peer))
    print()
    print(f"serve it:  synapse hub --federation-offer {args.bundle}")
    print("then read the bundle fingerprint to the peer operator out-of-band; they")
    print("compare it against their `synapse federation fetch` output before importing.")
    return 0


def _cmd_fetch(args: argparse.Namespace, *, fetcher: Fetcher = fetch_federation_offer) -> int:
    """Fetch a peer hub's offered bundle to a file and print its fingerprints — never import."""
    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        print(f"refusing to overwrite {out}; pass --force to replace it", file=sys.stderr)
        return 2
    try:
        peer = asyncio.run(
            fetcher(args.uri, local_id=args.local_id, token=args.token, timeout=args.timeout)
        )
    except FederationFetchError as exc:
        print(f"could not fetch the federation offer: {exc}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(encode_federation_offer(peer), indent=2) + "\n", encoding="utf-8")
    print(render_offer_fingerprints(peer))
    print()
    print(f"wrote the offered bundle to {out} — NOT imported.")
    print("compare the bundle fingerprint with the peer operator out-of-band (their")
    print("`synapse federation offer` prints the same block), then import explicitly:")
    print(f"  synapse federation import {out} --confirmed-by <operator> --source {args.uri}")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``federation`` command group."""
    parser = subparsers.add_parser(
        "federation",
        help="Import, list, and revoke out-of-band operator-confirmed peer domains.",
    )
    group = parser.add_subparsers(dest="federation_command", required=True)

    def _add_store(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--store", default=DEFAULT_STORE, help="Federation store path.")

    importer = group.add_parser("import", help="Import an out-of-band peer-domain bundle.")
    importer.add_argument("bundle", help="Path to the peer-domain bundle JSON file.")
    importer.add_argument(
        "--confirmed-by", required=True, help="Operator confirming the import (audit trail)."
    )
    importer.add_argument("--source", default=None, help="Where the bundle came from.")
    importer.add_argument(
        "--max-age",
        type=float,
        default=None,
        metavar="DAYS",
        help=(
            "Warn (import still succeeds) when the bundle never expires or expires "
            "further out than this many days — the trust-lifetime policy check at "
            "the cheapest moment, before the material lands in the store."
        ),
    )
    _add_store(importer)
    importer.set_defaults(func=_cmd_import)

    lister = group.add_parser(
        "list",
        help="List imported peer domains, their provenance, and each peering's age; "
        "--max-age DAYS flags stale active peerings and exits 1.",
    )
    lister.add_argument(
        "--max-age",
        type=float,
        default=None,
        metavar="DAYS",
        help=(
            "Flag active peerings imported longer ago than this many days as stale "
            "and exit 1 — trust material is a lifecycle, not a one-off ceremony."
        ),
    )
    _add_store(lister)
    lister.set_defaults(func=_cmd_list)

    revoker = group.add_parser("revoke", help="Revoke a peering (keeps its audit record).")
    revoker.add_argument("domain", help="Domain id of the peering to revoke.")
    _add_store(revoker)
    revoker.set_defaults(func=_cmd_revoke)

    offer = group.add_parser(
        "offer",
        help="Validate this domain's own bundle material and print its fingerprints "
        "(the offering side of the exchange ceremony).",
    )
    offer.add_argument("bundle", help="Path to this domain's own peer-bundle JSON file.")
    offer.set_defaults(func=_cmd_offer)

    fetch = group.add_parser(
        "fetch",
        help="Fetch a peer hub's offered bundle to a file and print its fingerprints; "
        "never imports — compare fingerprints out-of-band, then `federation import`.",
    )
    fetch.add_argument("uri", help="Peer hub websocket URI (ws:// or wss://).")
    fetch.add_argument("--out", required=True, help="File to write the fetched bundle to.")
    fetch.add_argument(
        "--local-id", default="federation-fetch", help="Identity stamped on the request frame."
    )
    fetch.add_argument("--token", default=None, help="Token for a secured peer hub.")
    fetch.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_FETCH_TIMEOUT,
        help="Seconds to wait for the offer before failing closed.",
    )
    fetch.add_argument("--force", action="store_true", help="Replace an existing --out file.")
    fetch.set_defaults(func=_cmd_fetch)
