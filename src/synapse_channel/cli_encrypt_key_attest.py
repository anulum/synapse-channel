# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hardware-attestation policy/evidence CLI
"""Attestation gating for at-rest keys: ``synapse encrypt-key attest-*``.

An HMAC policy binds an operator label and optional expected PCR digests;
evidence binds a nonce (fresh or supplied) and measured digests against that
policy; verification fails closed. The gate lets an encrypted workflow refuse
to open its key material on a host whose measured state does not match the
policy of record.
"""

from __future__ import annotations

import argparse


def _cmd_attest_policy_create(args: argparse.Namespace) -> int:
    """Create an HMAC attestation policy with optional expected PCR digests."""
    from synapse_channel.core.at_rest_attestation import create_hmac_policy, write_policy_file

    digests: dict[int, bytes] = {}
    for item in args.pcr or []:
        try:
            index_s, hex_digest = item.split("=", 1)
            digests[int(index_s)] = bytes.fromhex(hex_digest)
        except ValueError as exc:
            print(f"synapse encrypt-key attest-policy-create: invalid --pcr {item!r}: {exc}")
            return 2
    try:
        policy = create_hmac_policy(policy_id=args.policy_id, pcr_digests=digests)
        written = write_policy_file(args.path, policy)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(f"synapse encrypt-key attest-policy-create: {exc}")
        return 2
    print(f"wrote attestation policy (owner-only): {written}")
    return 0


def _cmd_attest_create(args: argparse.Namespace) -> int:
    """Create HMAC attestation evidence for a policy (fresh or supplied nonce)."""
    from synapse_channel.core.at_rest_attestation import (
        create_hmac_evidence,
        fresh_nonce,
        load_policy_file,
        write_evidence_file,
    )

    try:
        policy = load_policy_file(args.policy)
        nonce = bytes.fromhex(args.nonce) if args.nonce else fresh_nonce()
        digests: dict[int, bytes] | None = None
        if args.pcr:
            digests = {}
            for item in args.pcr:
                index_s, hex_digest = item.split("=", 1)
                digests[int(index_s)] = bytes.fromhex(hex_digest)
        evidence = create_hmac_evidence(policy, nonce=nonce, pcr_digests=digests)
        written = write_evidence_file(args.path, evidence)
    except FileExistsError as exc:
        print(str(exc))
        return 1
    except (ValueError, OSError) as exc:
        print(f"synapse encrypt-key attest-create: {exc}")
        return 2
    print(f"wrote attestation evidence (owner-only): {written}")
    print(f"nonce (hex): {evidence.nonce.hex()}")
    return 0


def _cmd_attest_verify(args: argparse.Namespace) -> int:
    """Verify attestation evidence against a policy (fail closed)."""
    from synapse_channel.core.at_rest_attestation import (
        enforce_attestation_gate,
        load_evidence_file,
        load_policy_file,
    )

    try:
        policy = load_policy_file(args.policy)
        evidence = load_evidence_file(args.evidence)
        enforce_attestation_gate(policy, evidence)
    except (ValueError, OSError) as exc:
        print(f"synapse encrypt-key attest-verify: {exc}")
        return 2
    print(f"attestation ok: policy={policy.policy_id} algorithm={policy.algorithm}")
    return 0


def add_attestation_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``attest-policy-create``/``attest-create``/``attest-verify`` subcommands."""
    attest_policy = nested.add_parser(
        "attest-policy-create",
        help="Create an HMAC hardware-attestation policy with optional expected PCR digests.",
    )
    attest_policy.add_argument("path", help="Destination policy file (must not already exist).")
    attest_policy.add_argument(
        "--policy-id",
        required=True,
        help="Operator label for this attestation policy.",
    )
    attest_policy.add_argument(
        "--pcr",
        action="append",
        default=[],
        help="Expected PCR as INDEX=HEX_SHA256 (repeatable).",
    )
    attest_policy.set_defaults(func=_cmd_attest_policy_create)

    attest_create = nested.add_parser(
        "attest-create",
        help="Create HMAC attestation evidence for a policy (binds a nonce and PCR digests).",
    )
    attest_create.add_argument("path", help="Destination evidence file (must not already exist).")
    attest_create.add_argument("--policy", required=True, help="Attestation policy file.")
    attest_create.add_argument(
        "--nonce",
        default=None,
        help="Optional challenge nonce as hex (a fresh nonce is drawn when omitted).",
    )
    attest_create.add_argument(
        "--pcr",
        action="append",
        default=[],
        help="Measured PCR as INDEX=HEX_SHA256 (defaults to the policy expectations).",
    )
    attest_create.set_defaults(func=_cmd_attest_create)

    attest_verify = nested.add_parser(
        "attest-verify",
        help="Verify attestation evidence against a policy (fail closed).",
    )
    attest_verify.add_argument("--policy", required=True, help="Attestation policy file.")
    attest_verify.add_argument("--evidence", required=True, help="Attestation evidence file.")
    attest_verify.set_defaults(func=_cmd_attest_verify)
