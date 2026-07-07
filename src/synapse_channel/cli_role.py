# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator CLI to grant, revoke, and list role-claim authorisations
"""``synapse role`` — manage which identities may claim which roles.

A role is a ``<project>/<role>`` address an agent asks to answer to on its
registration heartbeat. Without authorisation the hub binds any declared role, so a
socket can squat a privileged role. This command edits the deny-by-default
role-grant store (:mod:`synapse_channel.core.role_grants`) the hub consults when
``--require-role-claim`` is on: grant an identity the right to claim a role, revoke
it, or list what is granted. It only reads and writes the local store file — no hub,
no network — and the enforcement itself is opt-in on the hub, so editing the store
never changes an open hub's behaviour on its own.
"""

from __future__ import annotations

import argparse
import json

from synapse_channel.core.role_grants import (
    DEFAULT_STORE_PATH,
    RoleGrantError,
    RoleGrants,
    load_role_grants,
    save_role_grants,
)


def _add_store(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--store`` option pointing at the role-grant store file."""
    parser.add_argument(
        "--store",
        default=DEFAULT_STORE_PATH,
        help=f"Role-grant store path (default: {DEFAULT_STORE_PATH}).",
    )


def _cmd_grant(args: argparse.Namespace) -> int:
    """Grant ``--to`` the right to claim the named role, then persist the store."""
    try:
        grants = load_role_grants(args.store)
        updated = grants.with_grant(args.role, args.agent)
        save_role_grants(args.store, updated)
    except RoleGrantError as exc:
        print(f"role grant error: {exc}")
        return 2
    already = args.agent in grants.subjects_for(args.role)
    verb = "already granted" if already else "granted"
    print(f"{verb}: {args.agent} may claim {args.role}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    """Revoke ``--from``'s right to claim the named role, then persist the store."""
    try:
        grants = load_role_grants(args.store)
        present = args.agent in grants.subjects_for(args.role)
        updated = grants.without_grant(args.role, args.agent)
        save_role_grants(args.store, updated)
    except RoleGrantError as exc:
        print(f"role revoke error: {exc}")
        return 2
    if not present:
        print(f"not granted: {args.agent} did not hold {args.role} (no change)")
        return 1
    print(f"revoked: {args.agent} may no longer claim {args.role}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """List the grants for one role, or every role when none is named."""
    try:
        grants = load_role_grants(args.store)
    except RoleGrantError as exc:
        print(f"role list error: {exc}")
        return 2
    if args.role:
        listing = {args.role: list(grants.subjects_for(args.role))}
    else:
        listing = {role: list(grants.subjects_for(role)) for role in grants.roles()}
    if args.json:
        print(json.dumps({"grants": listing}, indent=2, sort_keys=True))
        return 0
    if not any(listing.values()):
        target = args.role if args.role else "any role"
        print(f"no role grants for {target}")
        return 0
    for role in sorted(listing):
        print(role)
        for subject in listing[role]:
            print(f"  {subject}")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``role`` subparser group."""
    role = subparsers.add_parser("role", help="Grant, revoke, and list role-claim authorisations.")
    nested = role.add_subparsers(dest="role_command", required=True)

    granter = nested.add_parser("grant", help="Grant an identity the right to claim a role.")
    granter.add_argument("role", help="Role address, '<project>/<role>'.")
    granter.add_argument(
        "--to",
        required=True,
        dest="agent",
        metavar="AGENT",
        help="Identity (or glob) permitted to claim it.",
    )
    _add_store(granter)
    granter.set_defaults(func=_cmd_grant)

    revoker = nested.add_parser("revoke", help="Revoke an identity's right to claim a role.")
    revoker.add_argument("role", help="Role address, '<project>/<role>'.")
    revoker.add_argument(
        "--from",
        required=True,
        dest="agent",
        metavar="AGENT",
        help="Identity (or glob) whose grant is removed.",
    )
    _add_store(revoker)
    revoker.set_defaults(func=_cmd_revoke)

    lister = nested.add_parser("list", help="List role-claim grants (all roles, or one).")
    lister.add_argument("role", nargs="?", default=None, help="Optional role to list.")
    lister.add_argument("--json", action="store_true", help="Emit the listing as JSON.")
    _add_store(lister)
    lister.set_defaults(func=_cmd_list)


__all__ = ["RoleGrants", "add_parsers"]
