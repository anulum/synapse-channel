# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity inventory audit + identity signing-key generation CLI
"""``synapse identity`` — audit, key generation, and governed pin recovery.

``audit`` is a local, read-only command: it loads an identity inventory file and
reports the ambiguities that would block an enforcement rollout — duplicate audit
subjects, missing credentials, and seats that run more than one agent id.

``keygen`` generates the Ed25519 key an agent uses to prove its identity under
connection-identity binding: it writes the private key to an owner-only file and
prints (or enrols) the public trust-bundle entry the hub verifies against. Neither
of those commands talks to a running hub.

``reclaim`` is the deliberately narrow live-hub recovery path for a stale
trust-on-first-use pin. It names the exact key the operator inspected, requires
an ACL grant and durable hub journal, and never replaces the key in place: after
the audited removal, a later valid proof may establish a new first-use pin.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.identity import IdentityError, IdentityInventory
from synapse_channel.core.identity_binding import IdentityBindingError, enroll_identity_key
from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.protocol import MessageType


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


async def _identity_reclaim(
    *,
    uri: str,
    operator: str,
    pin_name: str,
    expected_key_id: str,
    reason: str,
    break_glass: bool,
    token: str | None,
    ready_timeout: float,
    result_timeout: float,
    json_output: bool,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Request one governed pin reclaim and print the hub's authoritative verdict.

    Returns ``0`` when applied, ``1`` when policy refused the action, and ``2``
    when no authoritative verdict arrived. The client uses its ordinary machine
    identity, so the hub can require a pinned or operator-bundle-bound requester.
    """
    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") in {MessageType.IDENTITY_PIN_RECLAIM_RESULT, MessageType.ERROR}:
            replies.append(data)

    agent = agent_factory(operator, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent
        ):
            print(
                describe_connect_failure(
                    operator,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 2
        await agent.send_message(
            MessageType.IDENTITY_PIN_RECLAIM,
            target="System",
            pin_name=pin_name,
            expected_key_id=expected_key_id,
            reason=reason,
            break_glass=break_glass,
        )
        result = await _await_reclaim_result(replies, timeout=result_timeout)
        if result is None:
            print("pin reclaim failed: the hub returned no authoritative verdict")
            return 2
        if result.get("type") == MessageType.ERROR:
            rendered = {
                "applied": False,
                "pin_name": pin_name,
                "expected_key_id": expected_key_id,
                "detail": str(result.get("payload") or "hub refused the request"),
            }
        else:
            rendered = {
                "applied": bool(result.get("applied")),
                "pin_name": str(result.get("pin_name") or pin_name),
                "expected_key_id": str(result.get("expected_key_id") or expected_key_id),
                "break_glass": bool(result.get("break_glass")),
                "audit_seq": result.get("audit_seq"),
                "detail": str(result.get("payload") or ""),
            }
        if json_output:
            print(json.dumps(rendered, indent=2, sort_keys=True))
        elif rendered["applied"]:
            suffix = f" (audit seq {rendered['audit_seq']})" if rendered.get("audit_seq") else ""
            print(f"reclaimed identity pin for {rendered['pin_name']}{suffix}")
        else:
            print(f"pin reclaim refused: {rendered['detail']}")
        return 0 if rendered["applied"] else 1
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


async def _await_reclaim_result(
    replies: list[dict[str, Any]], *, timeout: float
) -> dict[str, Any] | None:
    """Return the first reclaim verdict or generic hub error before ``timeout``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    while loop.time() <= deadline:
        if replies:
            return replies[-1]
        await asyncio.sleep(0.01)
    return None


def _cmd_identity_reclaim(args: argparse.Namespace) -> int:
    """Dispatch ``synapse identity reclaim`` to the one-shot async client."""
    return asyncio.run(
        _identity_reclaim(
            uri=args.uri,
            operator=args.operator,
            pin_name=args.pin_name,
            expected_key_id=args.expected_key_id,
            reason=args.reason,
            break_glass=args.break_glass,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
            json_output=args.json,
        )
    )


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

    reclaim = nested.add_parser(
        "reclaim",
        help="Remove one stale TOFU identity pin through the hub's governed operator path.",
    )
    reclaim.add_argument("pin_name", help="Pinned agent identity to reclaim.")
    reclaim.add_argument(
        "--operator",
        required=True,
        help="Cryptographically bound requester identity named by the ACL grant.",
    )
    reclaim.add_argument(
        "--expected-key-id",
        required=True,
        help="Exact current pin key id (compare-and-swap guard against a stale request).",
    )
    reclaim.add_argument(
        "--reason", required=True, help="Operator reason written to the durable audit event."
    )
    reclaim.add_argument(
        "--break-glass",
        action="store_true",
        help="Explicitly evict a live or still-leased holder; loudly marked in the audit.",
    )
    reclaim.add_argument("--uri", default=default_hub_uri())
    reclaim.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    reclaim.add_argument(
        "--token-file",
        default=None,
        help="Read the shared-secret token from this file instead of --token.",
    )
    reclaim.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    reclaim.add_argument(
        "--timeout", type=float, default=5.0, help="Seconds to await the governed verdict."
    )
    reclaim.add_argument("--json", action="store_true", help="Emit the verdict as JSON.")
    reclaim.set_defaults(func=_cmd_identity_reclaim)
