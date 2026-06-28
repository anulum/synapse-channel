# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shadow-mode ACL evaluation CLI
"""``synapse acl shadow`` — record would-allow/would-deny ACL decisions.

A local, read-only command: it evaluates declared candidate access requests
against an ACL policy and prints the deny-by-default decision each request would
receive under enforcement. It is shadow mode — it never blocks anything and
always exits 0 — so an operator can review which rule would match before turning
on enforcement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from synapse_channel.core.acl import AclError, Target, evaluate_access, load_acl_policy

_GLYPH = {"would_allow": "+", "would_deny": "-"}


def _load_requests(path: str | Path) -> list[dict[str, Any]]:
    """Load candidate access requests from a JSON list, raising AclError on error."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AclError(f"requests file does not exist: {path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AclError(f"invalid requests JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise AclError("requests file must be a JSON list of objects")
    return data


def _cmd_acl_shadow(args: argparse.Namespace) -> int:
    """Run ``synapse acl shadow`` and return its exit code (always 0 unless input fails)."""
    try:
        policy = load_acl_policy(args.policy)
        requests = _load_requests(args.requests)
    except AclError as exc:
        print(f"acl shadow error: {exc}")
        return 2
    decisions = []
    for request in requests:
        decision = evaluate_access(
            subject=str(request.get("subject", "")),
            project=str(request.get("project", "") or args.project),
            permission=str(request.get("permission", "")),
            target=Target(
                str(request.get("target_kind", "")), str(request.get("target_value", ""))
            ),
            policy=policy,
        )
        decisions.append(decision)
    allowed = sum(1 for decision in decisions if decision.decision == "would_allow")
    denied = len(decisions) - allowed
    if args.json:
        print(
            json.dumps(
                {
                    "mode": "shadow",
                    "would_allow": allowed,
                    "would_deny": denied,
                    "decisions": [decision.as_dict() for decision in decisions],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"acl shadow [{args.project}]: {allowed} would-allow, {denied} would-deny")
        for decision in decisions:
            glyph = _GLYPH.get(decision.decision, "?")
            print(
                f"  {glyph} {decision.subject} {decision.permission} "
                f"{decision.target.kind}:{decision.target.value} -> {decision.reason}"
            )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``acl`` subparser group."""
    acl = subparsers.add_parser("acl", help="Shadow-mode ACL evaluation (non-blocking).")
    nested = acl.add_subparsers(dest="acl_command", required=True)

    shadow = nested.add_parser(
        "shadow", help="Record would-allow/would-deny decisions for candidate accesses."
    )
    shadow.add_argument("--policy", required=True, help="ACL policy JSON file.")
    shadow.add_argument("--requests", required=True, help="Candidate access requests JSON file.")
    shadow.add_argument("--project", default="", help="Default project namespace for requests.")
    shadow.add_argument("--json", action="store_true", help="Emit the shadow report as JSON.")
    shadow.set_defaults(func=_cmd_acl_shadow)
