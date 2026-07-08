# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity inventory audit + identity signing-key generation CLI
"""``synapse identity`` — audit declared identities and generate identity signing keys.

``audit`` is a local, read-only command: it loads an identity inventory file and
reports the ambiguities that would block an enforcement rollout — duplicate audit
subjects, missing credentials, and seats that run more than one agent id.

``keygen`` generates the Ed25519 key an agent uses to prove its identity under
connection-identity binding: it writes the private key to an owner-only file and
prints (or enrols) the public trust-bundle entry the hub verifies against. Neither
command talks to a running hub.
"""

from __future__ import annotations

import argparse
import json

from synapse_channel.core.identity import IdentityError, IdentityInventory
from synapse_channel.core.identity_binding import IdentityBindingError, enroll_identity_key
from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)


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


def _cmd_identity_keygen(args: argparse.Namespace) -> int:
    """Generate an identity signing key and print or enrol its public trust entry.

    Returns ``2`` when the private key or trust bundle cannot be written (for example
    the key file already exists, or the key id is already enrolled), else ``0``.
    """
    try:
        private_key = generate_signing_key()
        write_signing_key(args.private_out, private_key)
        pub_b64 = public_key_b64(private_key)
        if args.trust:
            enroll_identity_key(
                args.trust,
                key_id=args.key_id,
                public_key_b64=pub_b64,
                senders=[args.sender],
                expires_at=args.expires_at,
            )
    except (IdentityKeyError, IdentityBindingError) as exc:
        print(f"identity keygen error: {exc}")
        return 2
    if args.trust:
        print(
            f"wrote identity key to {args.private_out}; enrolled {args.key_id} for "
            f"{args.sender} in {args.trust}"
        )
        return 0
    entry: dict[str, object] = {
        "key_id": args.key_id,
        "public_key": pub_b64,
        "senders": [args.sender],
    }
    if args.expires_at is not None:
        entry["expires_at"] = args.expires_at
    print(
        f"wrote identity key to {args.private_out}. Enrol this public entry in the hub's "
        "--identity-trust bundle:"
    )
    print(json.dumps({"keys": [entry]}, indent=2, sort_keys=True))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``identity`` subparser group."""
    identity = subparsers.add_parser(
        "identity", help="Audit identities and generate identity signing keys."
    )
    nested = identity.add_subparsers(dest="identity_command", required=True)

    audit = nested.add_parser("audit", help="Audit an identity inventory for rollout blockers.")
    audit.add_argument("--identities", required=True, help="Identity inventory JSON file.")
    audit.add_argument("--json", action="store_true", help="Emit the audit as JSON.")
    audit.set_defaults(func=_cmd_identity_audit)

    keygen = nested.add_parser(
        "keygen", help="Generate an Ed25519 identity key and its trust-bundle entry."
    )
    keygen.add_argument(
        "--sender", required=True, help="Audit subject the key proves, e.g. PROJECT/agent-id."
    )
    keygen.add_argument(
        "--key-id", required=True, help="Public key id recorded in the trust bundle."
    )
    keygen.add_argument(
        "--private-out",
        required=True,
        metavar="FILE",
        help="Where to write the private key (owner-only PEM; never overwritten).",
    )
    keygen.add_argument(
        "--trust",
        default="",
        metavar="FILE",
        help="Also enrol the public key in this identity trust bundle.",
    )
    keygen.add_argument(
        "--expires-at",
        type=float,
        default=None,
        metavar="TS",
        help="Optional key expiry as wall-clock seconds since the epoch.",
    )
    keygen.set_defaults(func=_cmd_identity_keygen)
