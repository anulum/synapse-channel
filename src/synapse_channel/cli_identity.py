# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity inventory audit CLI
"""``synapse identity audit`` — inventory and audit declared agent identities.

A local, read-only command: it loads an identity inventory file and reports the
ambiguities that would block an enforcement rollout — duplicate audit subjects,
missing credentials, and seats that run more than one agent id. It does not issue
or verify credentials and does not change how the hub admits a connection.
"""

from __future__ import annotations

import argparse
import json

from synapse_channel.core.identity import IdentityError, IdentityInventory


def _cmd_identity_audit(args: argparse.Namespace) -> int:
    """Run ``synapse identity audit`` and return its exit code.

    Returns ``2`` on a malformed inventory, ``1`` when a ``fail`` finding exists
    (a duplicate identity), and ``0`` otherwise.
    """
    try:
        inventory = IdentityInventory.from_file(args.identities)
    except IdentityError as exc:
        print(f"identity error: {exc}")
        return 2
    findings = inventory.audit()
    if args.json:
        print(
            json.dumps(
                {
                    "identities": [identity.as_dict() for identity in inventory.identities()],
                    "findings": [finding.as_dict() for finding in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"identities: {len(inventory.identities())}")
        for identity in inventory.identities():
            credential = "yes" if identity.credential_id else "MISSING"
            print(
                f"  {identity.audit_subject} seat={identity.seat_id or '-'} credential={credential}"
            )
        for finding in findings:
            print(f"  [{finding.severity}] {finding.subject}: {finding.message}")
    return 1 if any(finding.severity == "fail" for finding in findings) else 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``identity`` subparser group."""
    identity = subparsers.add_parser("identity", help="Inventory and audit agent identities.")
    nested = identity.add_subparsers(dest="identity_command", required=True)

    audit = nested.add_parser("audit", help="Audit an identity inventory for rollout blockers.")
    audit.add_argument("--identities", required=True, help="Identity inventory JSON file.")
    audit.add_argument("--json", action="store_true", help="Emit the audit as JSON.")
    audit.set_defaults(func=_cmd_identity_audit)
