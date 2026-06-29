# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse federation` CLI: import, list, and revoke peer domains
"""``synapse federation`` — import, list, and revoke operator-confirmed peer domains.

Trust between Synapse domains is established out-of-band: an operator receives a peer
domain's bundle through a trusted channel and imports it explicitly. ``import`` reads
that bundle file, requires a ``--confirmed-by`` operator, records the provenance, and
adds the peering to the local store; ``list`` shows the imported peerings and their
provenance; ``revoke`` marks a peering revoked so it fails authorisation while keeping
its audit record. There is no auto-discovery and no trust-on-first-use — every peering
is auditable back to a human decision.

The policy lives in :mod:`synapse_channel.core.federation` and the persistence in
:mod:`synapse_channel.core.federation_store`; this is the thin I/O shell, with an
injectable clock and store path so the commands are testable without a real home.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.federation_store import (
    FederationRecord,
    FederationStoreError,
    PeerProvenance,
    load_store,
    merge_record,
    peer_from_dict,
    save_store,
)

Clock = Callable[[], float]

DEFAULT_STORE = "~/.synapse/federation.json"
"""Default operator-level federation store path."""


def _store_path(args: argparse.Namespace) -> Path:
    """Return the federation store path, expanding ``~`` and any override."""
    return Path(args.store).expanduser()


def _cmd_import(args: argparse.Namespace, *, clock: Clock = time.time) -> int:
    """Import an out-of-band peer-domain bundle with operator-confirmed provenance."""
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


def _cmd_list(args: argparse.Namespace) -> int:
    """List imported peer domains and the provenance of each peering."""
    try:
        records = load_store(_store_path(args))
    except FederationStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not records:
        print("no peer domains imported")
        return 0
    print(f"{len(records)} peer domain(s):")
    for domain_id in sorted(records):
        record = records[domain_id]
        peer = record.peer
        state = "revoked" if peer.revoked else "active"
        namespaces = ", ".join(sorted(peer.namespaces)) or "(none)"
        print(
            f"  {domain_id} [{state}] namespaces={namespaces} "
            f"keys={len(peer.signing_key_ids)} pins={len(peer.certificate_pins)} "
            f"scope={len(peer.scope_grants)} "
            f"— confirmed by {record.provenance.confirmed_by} from {record.provenance.source}"
        )
    return 0


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
    _add_store(importer)
    importer.set_defaults(func=_cmd_import)

    lister = group.add_parser("list", help="List imported peer domains and their provenance.")
    _add_store(lister)
    lister.set_defaults(func=_cmd_list)

    revoker = group.add_parser("revoke", help="Revoke a peering (keeps its audit record).")
    revoker.add_argument("domain", help="Domain id of the peering to revoke.")
    _add_store(revoker)
    revoker.set_defaults(func=_cmd_revoke)
