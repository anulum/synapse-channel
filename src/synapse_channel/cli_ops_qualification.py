# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ops gate and sign-qualification commands
"""``synapse ops gate`` and ``synapse ops sign-qualification``.

``gate`` prints, for one exact catalogue row, whether it may run under the
``personal`` or ``production`` profile and on what basis; it exits 0 when it
may and 1 when it may not. ``sign-qualification`` signs a qualification
bundle body with an owner-only Ed25519 key. Neither command runs an
operation.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.secret_files import SecretFileError, read_secret_file
from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.qualification_bundle import (
    PROFILES,
    QualificationBundleError,
    gate,
    sign_bundle,
)
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.spec import OperationSpecError


def json_file(path: Path) -> Any:
    """Read one JSON document, naming the file when it is not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QualificationBundleError(f"{path.name} is not JSON") from exc


def trusted_keys(path: Path | None) -> dict[str, bytes]:
    """Read ``{"keys": {key_id: base64 raw Ed25519 public key}}``."""
    if path is None:
        return {}
    document = json_file(path)
    keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(keys, dict) or not keys:
        raise QualificationBundleError("the trust file maps key ids to public keys")
    trusted: dict[str, bytes] = {}
    for key_id, encoded in keys.items():
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except ValueError as exc:
            raise QualificationBundleError("a trusted key is not base64") from exc
        if len(raw) != 32:
            raise QualificationBundleError("a trusted key is a raw 32-byte Ed25519 public key")
        trusted[str(key_id)] = raw
    return trusted


def _one_row(args: argparse.Namespace) -> OperationRow:
    from synapse_channel.cli_ops import default_catalogue_path, load_catalogue

    path = Path(args.catalogue) if args.catalogue else default_catalogue_path()
    catalogue = load_catalogue(path)
    if not isinstance(catalogue, Catalogue):
        raise QualificationBundleError("a catalogue of the earlier schema is never runnable")
    rows = [
        row
        for row in catalogue.rows(host=args.host, vendor=args.vendor, transport=args.transport)
        if row.operation == args.operation and args.variant in (None, row.variant)
    ]
    if len(rows) != 1:
        raise QualificationBundleError(
            f"{len(rows)} rows match; name --host, --vendor, --transport and --variant to pick one"
        )
    return rows[0]


def _cmd_gate(args: argparse.Namespace) -> int:
    """Print the gate verdict for one row."""
    try:
        row = _one_row(args)
        envelope = json_file(args.bundle) if args.bundle else None
        verdict = gate(
            row,
            profile=args.profile,
            now=int(time.time()),
            envelope=envelope,
            trusted_keys=trusted_keys(args.trust),
            required_environment=args.require_environment,
        )
    except (OSError, QualificationBundleError, OperationSpecError) as exc:
        print(f"synapse ops gate: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(verdict.to_json(), sort_keys=True))
    return 0 if verdict.runnable else 1


def _cmd_sign(args: argparse.Namespace) -> int:
    """Sign a bundle body with an owner-only PEM key and write the envelope."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        pem = read_secret_file(args.key_file, flag="--key-file").encode("ascii")
        key = load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise QualificationBundleError("the key file holds no Ed25519 private key")
        envelope = sign_bundle(json_file(args.bundle_body), key, args.key_id)
        args.out.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", "utf-8")
    except (OSError, ValueError, SecretFileError) as exc:
        print(f"synapse ops sign-qualification: {exc}", file=sys.stderr)
        return 2
    return 0


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``gate`` and ``sign-qualification`` below ``synapse ops``.

    Parameters
    ----------
    commands : argparse._SubParsersAction
        The ``synapse ops`` subcommand group.
    """
    check = commands.add_parser(
        "gate", help="Say whether one row may run under a profile, and why; runs nothing."
    )
    check.add_argument("operation", help="Dotted operation name.")
    check.add_argument("--profile", required=True, choices=sorted(PROFILES))
    check.add_argument("--bundle", type=Path, help="Signed qualification bundle (production).")
    check.add_argument("--trust", type=Path, help="Trusted qualifier keys (production).")
    check.add_argument(
        "--require-environment",
        action="append",
        default=[],
        help="Environment component the profile names; repeat for each.",
    )
    check.add_argument("--catalogue", default=None, help="Catalogue file to read.")
    check.add_argument("--host", default=None, help="Host of the row.")
    check.add_argument("--vendor", default=None, help="Vendor of the row.")
    check.add_argument("--transport", default=None, help="Transport of the row.")
    check.add_argument("--variant", default=None, help="Variant of the row.")
    check.set_defaults(func=_cmd_gate)

    sign = commands.add_parser(
        "sign-qualification", help="Sign a qualification bundle body with an owner-only key."
    )
    sign.add_argument("--bundle-body", required=True, type=Path, help="Bundle body JSON.")
    sign.add_argument("--key-file", required=True, help="Owner-only Ed25519 private key PEM.")
    sign.add_argument("--key-id", required=True, help="Id under which verifiers trust the key.")
    sign.add_argument("--out", required=True, type=Path, help="Envelope file to write.")
    sign.set_defaults(func=_cmd_sign)
