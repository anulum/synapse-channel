# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed capability-card lifecycle CLI
"""``synapse capability-card`` key generation, signing, and verification."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.capability_card_signing import (
    DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
    CapabilityCardSigningError,
    load_capability_card_json,
    sign_capability_card,
)
from synapse_channel.core.capability_card_trust import (
    CapabilityCardTrustError,
    enroll_capability_card_key,
    load_capability_card_trust_bundle,
)
from synapse_channel.core.capability_card_verification import (
    CapabilityCardVerificationResult,
    verify_capability_card,
)
from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    load_signing_key,
    public_key_b64,
    write_signing_key,
)


def _cmd_keygen(args: argparse.Namespace) -> int:
    """Generate a profile-separated signing key and optional trust entry."""
    created_key: Path | None = None
    try:
        key_id, agents, projects, expires_at = _keygen_fields(args)
        private_key = generate_signing_key()
        write_signing_key(args.private_out, private_key)
        created_key = Path(args.private_out).expanduser()
        public_key = public_key_b64(private_key)
        if args.trust:
            enroll_capability_card_key(
                args.trust,
                key_id=key_id,
                public_key_b64=public_key,
                agents=agents,
                projects=projects,
                expires_at=expires_at,
            )
    except (CapabilityCardTrustError, IdentityKeyError, ImportError, OSError) as exc:
        cleanup_error = ""
        if created_key is not None and args.trust:
            try:
                created_key.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                cleanup_error = f"; generated key could not be removed: {cleanup_exc}"
        print(f"capability-card keygen error: {exc}{cleanup_error}", file=sys.stderr)
        return 2
    if args.trust:
        print(f"wrote capability-card key to {args.private_out}; enrolled {key_id} in {args.trust}")
        return 0
    entry: dict[str, Any] = {
        "agents": list(agents),
        "key_id": key_id,
        "projects": list(projects),
        "public_key": public_key,
    }
    if expires_at is not None:
        entry["expires_at"] = expires_at
    print(f"wrote capability-card key to {args.private_out}. Enrol this public entry:")
    print(json.dumps({"keys": [entry]}, indent=2, sort_keys=True))
    return 0


def _cmd_sign(args: argparse.Namespace) -> int:
    """Sign one canonical card JSON file without overwriting inputs."""
    try:
        card = load_capability_card_json(args.card)
        if args.agent:
            card["agent"] = args.agent
        if args.project:
            card["project"] = args.project
        if args.manifest_digest:
            card["manifest_digest"] = args.manifest_digest
        private_key = load_signing_key(args.key)
        signed = sign_capability_card(
            card,
            key_id=args.key_id,
            private_key=private_key,
            sequence=args.sequence,
            signed_at=args.signed_at,
            expires_at=args.expires_at,
            lifetime_seconds=args.lifetime_seconds,
        )
        rendered = json.dumps(signed, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.out:
            _write_new(args.out, rendered)
        else:
            print(rendered, end="")
    except (CapabilityCardSigningError, IdentityKeyError, ImportError, OSError) as exc:
        print(f"capability-card sign error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        print(f"wrote signed capability card to {args.out}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Verify one card cryptographically without mutating hub lifecycle state."""
    try:
        card = load_capability_card_json(args.card)
        trust = load_capability_card_trust_bundle(
            args.trust,
            clock_skew_seconds=args.clock_skew_seconds,
        )
        agent = args.agent or str(card.get("agent") or "").strip()
        project = args.project or str(card.get("project") or "").strip()
        if not agent or not project:
            raise CapabilityCardSigningError(
                "verification requires agent and project in the card or explicit flags"
            )
        verification = verify_capability_card(
            card,
            trust_bundle=trust,
            now=time.time() if args.now is None else args.now,
            required_agent=agent,
            required_project=project,
            required_manifest_digest=args.manifest_digest,
            remember=False,
        )
    except (CapabilityCardSigningError, CapabilityCardTrustError) as exc:
        print(f"capability-card verify error: {exc}", file=sys.stderr)
        return 2
    payload = verification.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['result']}: {payload['detail']}")
        if payload.get("key_id"):
            print(
                f"key={payload['key_id']} sequence={payload.get('sequence', '-')} "
                f"digest={payload.get('card_digest', '-')}"
            )
    return 0 if verification.result is CapabilityCardVerificationResult.VALID else 1


def _keygen_fields(
    args: argparse.Namespace,
) -> tuple[str, tuple[str, ...], tuple[str, ...], float | None]:
    """Return normalized keygen fields before any private file is created."""
    key_id = str(args.key_id).strip()
    agents = tuple(str(value).strip() for value in args.agent)
    projects = tuple(str(value).strip() for value in args.project)
    if not key_id:
        raise CapabilityCardTrustError("capability-card key needs a non-empty key_id")
    if not agents or any(not value for value in agents):
        raise CapabilityCardTrustError("capability-card key needs non-empty agent bindings")
    if not projects or any(not value for value in projects):
        raise CapabilityCardTrustError("capability-card key needs non-empty project bindings")
    expires_at = args.expires_at
    if expires_at is not None and not math.isfinite(expires_at):
        raise CapabilityCardTrustError("capability-card key expires_at must be finite")
    return key_id, agents, projects, expires_at


def _write_new(path: str | Path, text: str) -> None:
    """Create an owner-only output file and refuse to replace an existing card."""
    file = Path(path).expanduser()
    file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(file, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        file.unlink(missing_ok=True)
        raise


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``capability-card`` lifecycle commands."""
    group = subparsers.add_parser(
        "capability-card",
        help="Generate, sign, and verify advisory capability-card credentials.",
    )
    nested = group.add_subparsers(dest="capability_card_command", required=True)

    keygen = nested.add_parser(
        "keygen", help="Generate a separate Ed25519 card-signing key and trust entry."
    )
    keygen.add_argument("--key-id", required=True, help="Public id recorded in signatures.")
    keygen.add_argument("--private-out", required=True, metavar="FILE")
    keygen.add_argument(
        "--agent", action="append", required=True, help="Agent identity this key may sign."
    )
    keygen.add_argument(
        "--project", action="append", required=True, help="Project namespace this key may sign."
    )
    keygen.add_argument(
        "--trust", default="", metavar="FILE", help="Also enrol into this separate trust file."
    )
    keygen.add_argument("--expires-at", type=float, default=None, metavar="TS")
    keygen.set_defaults(func=_cmd_keygen)

    sign = nested.add_parser("sign", help="Sign one canonical capability-card JSON object.")
    sign.add_argument("card", metavar="CARD.json")
    sign.add_argument("--key", required=True, metavar="PRIVATE.pem")
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--sequence", type=int, required=True)
    sign.add_argument("--agent", default="", help="Override the card agent before signing.")
    sign.add_argument("--project", default="", help="Override the card project before signing.")
    sign.add_argument("--manifest-digest", default="")
    sign.add_argument("--signed-at", type=float, default=None, metavar="TS")
    sign.add_argument("--expires-at", type=float, default=None, metavar="TS")
    sign.add_argument(
        "--lifetime-seconds",
        type=float,
        default=DEFAULT_CAPABILITY_CARD_LIFETIME_SECONDS,
        metavar="SECONDS",
    )
    sign.add_argument("--out", default="", metavar="FILE", help="Create this file; never replace.")
    sign.set_defaults(func=_cmd_sign)

    verify = nested.add_parser("verify", help="Verify one card against a separate trust bundle.")
    verify.add_argument("card", metavar="CARD.json")
    verify.add_argument("--trust", required=True, metavar="FILE")
    verify.add_argument("--agent", default="", help="Required agent; defaults to the card.")
    verify.add_argument("--project", default="", help="Required project; defaults to the card.")
    verify.add_argument("--manifest-digest", default="")
    verify.add_argument("--clock-skew-seconds", type=float, default=30.0)
    verify.add_argument("--now", type=float, default=None, metavar="TS")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=_cmd_verify)
