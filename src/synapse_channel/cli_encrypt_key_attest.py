# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator HMAC statement policy/evidence CLI
"""Attestation gating for at-rest keys: ``synapse encrypt-key attest-*``.

An HMAC policy binds an operator label and optional expected PCR digests;
evidence binds a random or supplied nonce and caller-declared digests against
that policy. Verification checks the HMAC and expected values, not hardware
measurements, TPM quotes or freshness. No verifier-issued challenge is matched
or consumed. Evidence creation defaults its digest values to the policy itself.
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
    """Sign caller-declared digests with an operator HMAC key and a nonce.

    Omitted digests are copied from the policy; no host measurements are read.
    A generated nonce is random, without verifier-issued challenge state.
    """
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
    """Verify a software HMAC statement and expected digests against a policy.

    Invalid statements are refused. A successful check does not verify a
    hardware quote, measurement origin or freshness; repeat checks are allowed.
    """
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
    print(f"software attestation ok: policy={policy.policy_id} algorithm={policy.algorithm}")
    print("verified operator HMAC statement only; hardware quote and freshness not verified")
    return 0


def add_attestation_parsers(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``attest-policy-create``/``attest-create``/``attest-verify`` subcommands."""
    attest_policy = nested.add_parser(
        "attest-policy-create",
        help="Create a software HMAC policy for caller-declared PCR values; no hardware quote.",
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
        help="Sign declared PCR values with an operator HMAC key; no measurements collected.",
    )
    attest_create.add_argument("path", help="Destination evidence file (must not already exist).")
    attest_create.add_argument("--policy", required=True, help="Attestation policy file.")
    attest_create.add_argument(
        "--nonce",
        default=None,
        help="Nonce as hex (random when omitted); no verifier challenge or replay state.",
    )
    attest_create.add_argument(
        "--pcr",
        action="append",
        default=[],
        help="Caller-declared PCR as INDEX=HEX_SHA256 (defaults to policy; no collection).",
    )
    attest_create.set_defaults(func=_cmd_attest_create)

    attest_verify = nested.add_parser(
        "attest-verify",
        help="Verify a software HMAC statement; no hardware quote or freshness verification.",
    )
    attest_verify.add_argument("--policy", required=True, help="Attestation policy file.")
    attest_verify.add_argument("--evidence", required=True, help="Attestation evidence file.")
    attest_verify.set_defaults(func=_cmd_attest_verify)
