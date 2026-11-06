# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sign and verify policy files for policy-check
"""``synapse policy sign`` and ``synapse policy verify``.

``sign`` writes a signed copy of a JSON policy to a new owner-only file (or to
stdout) and never replaces its input. ``verify`` checks a signed policy against
an Ed25519 trust bundle and prints its key id, version and digest. Both exit
``2`` on any refusal, with the reason on stderr and no key material.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from synapse_channel.cli_capability_cards import _write_new
from synapse_channel.core.identity_keys import IdentityKeyError, load_signing_key
from synapse_channel.core.policy_engine import PolicyError
from synapse_channel.core.policy_signing import (
    load_policy_document,
    policy_provenance,
    sign_policy_document,
)


def load_trust_bundle(path: str | Path) -> dict[str, Any]:
    """Load a policy trust bundle JSON object.

    Raises
    ------
    PolicyError
        When the file is missing, is not JSON, or is not an object.
    """
    target = Path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"policy trust bundle does not exist: {target}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"invalid policy trust bundle JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise PolicyError("policy trust bundle must be a JSON object")
    return document


def _cmd_sign(args: argparse.Namespace) -> int:
    """Sign one JSON policy into a new file or stdout."""
    try:
        document = load_policy_document(args.policy)
        signed = sign_policy_document(
            document, private_key=load_signing_key(args.key), key_id=args.key_id
        )
        rendered = json.dumps(signed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.out:
            _write_new(args.out, rendered)
        else:
            sys.stdout.write(rendered)
    except FileExistsError:
        print(f"policy sign: refusing to replace existing {args.out}", file=sys.stderr)
        return 2
    except (PolicyError, IdentityKeyError, OSError) as exc:
        print(f"policy sign: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Verify one signed policy against a trust bundle."""
    try:
        provenance = policy_provenance(
            load_policy_document(args.policy),
            trust_document=load_trust_bundle(args.trust_bundle),
            min_policy_version=args.min_policy_version,
        )
    except PolicyError as exc:
        print(f"policy verify: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(provenance.as_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"policy verified: version {provenance.policy_version}, key {provenance.key_id}, "
            f"{provenance.digest}"
        )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse policy sign`` and ``synapse policy verify``."""
    group = subparsers.add_parser(
        "policy", help="Sign and verify policy files for policy-check (Ed25519)."
    )
    nested = group.add_subparsers(dest="policy_command", required=True)
    sign = nested.add_parser("sign", help="Write a signed copy of one JSON policy.")
    sign.add_argument("policy", help="JSON policy file; it must declare policy_version.")
    sign.add_argument("--key", required=True, metavar="PRIVATE.pem")
    sign.add_argument("--key-id", required=True)
    sign.add_argument(
        "--out", default="", metavar="FILE", help="New owner-only output file (default: stdout)."
    )
    sign.set_defaults(func=_cmd_sign)
    verify = nested.add_parser("verify", help="Verify a signed policy against a trust bundle.")
    verify.add_argument("policy", help="Signed JSON policy file.")
    verify.add_argument("--trust-bundle", required=True, metavar="FILE")
    verify.add_argument(
        "--min-policy-version",
        type=int,
        default=None,
        metavar="N",
        help="Refuse a policy whose policy_version is below N.",
    )
    verify.add_argument("--json", action="store_true", help="Print the provenance as JSON.")
    verify.set_defaults(func=_cmd_verify)
