# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — `synapse spend`: operate a pool owner's ledger and ask an owner (F02)
"""``synapse spend``: the pool owner's local operator commands, and one peer request.

The operator commands act on the owner-only ledger file on the owner host. They are
the only way to configure a pool or reconcile a reservation; neither is ever sent
over the wire:

- ``configure --file POOL.json`` appends one pool configuration;
- ``status --pool ID`` prints the bound, the charged and held amounts and the headroom;
- ``audit --pool ID`` prints every recorded event, including refusals with reasons;
- ``reconcile --file RECONCILE.json`` closes a reservation with an evidenced amount.

``request reserve|settle|query --uri OWNER --local-id HUB --file DOC.json`` sends one
peer request to an owner hub and prints its answer. Its exit code is 0 when the
owner admitted, settled or found; 1 when it refused; 2 on an error or timeout.
After a timeout, send ``query`` with the same seat, task, operation and key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from synapse_channel.core.identity_keys import IdentityKeyError
from synapse_channel.core.peer_identity import (
    PeerRegistrationSigner,
    load_peer_registration_signer,
)
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes
from synapse_channel.core.spend_ledger import SpendLedger, SpendLedgerError
from synapse_channel.core.spend_transport import (
    DEFAULT_SPEND_TIMEOUT,
    SpendTransportError,
    request_spend,
)
from synapse_channel.core.spend_wire import SPEND_ACTIONS

_SUCCESS = {"reserve": "admitted", "settle": "settled", "query": "found"}


def _document(path: str) -> dict[str, Any]:
    """Load one owner-only JSON object without following a symlink."""
    raw = read_owner_only_file_bytes(path, purpose="spend input", max_bytes=65536)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpendLedgerError("spend input must be a UTF-8 JSON object") from exc
    if not isinstance(decoded, dict):
        raise SpendLedgerError("spend input must be a JSON object")
    return decoded


def _print(document: object) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def _operator(args: argparse.Namespace) -> int:
    """Run one local operator command on the owner-only ledger."""
    now = datetime.now(timezone.utc)
    try:
        ledger = SpendLedger(args.ledger, owner_hub_id=args.hub_id)
        if args.spend_command == "configure":
            _print(ledger.configure(_document(args.file), now=now))
        elif args.spend_command == "reconcile":
            _print(ledger.reconcile(_document(args.file), now=now))
        elif args.spend_command == "status":
            _print(ledger.status(args.pool, now=now))
        else:
            _print(ledger.audit(args.pool))
    except (SpendLedgerError, SecurePathError) as exc:
        print(f"synapse spend: {exc}", file=sys.stderr)
        return 2
    return 0


def _signer(args: argparse.Namespace) -> PeerRegistrationSigner | None:
    if bool(args.peer_identity_key) != bool(args.peer_identity_key_id):
        raise ValueError("--peer-identity-key and --peer-identity-key-id go together")
    if not args.peer_identity_key:
        return None
    return load_peer_registration_signer(args.peer_identity_key, args.peer_identity_key_id)


def _token(args: argparse.Namespace) -> str | None:
    if not args.token_file:
        return None
    raw = read_owner_only_file_bytes(args.token_file, purpose="connect token", max_bytes=4096)
    return raw.decode("utf-8").strip()


def _request(args: argparse.Namespace) -> int:
    """Send one peer request to an owner hub and print its answer."""
    try:
        document = _document(args.file)
        result = asyncio.run(
            request_spend(
                args.action,
                document,
                uri=args.uri,
                local_id=args.local_id,
                token=_token(args),
                timeout=args.timeout,
                signer=_signer(args),
            )
        )
    except (
        SpendLedgerError,
        SecurePathError,
        SpendTransportError,
        IdentityKeyError,
        ValueError,
    ) as exc:
        print(f"synapse spend: {exc}", file=sys.stderr)
        return 2
    _print(result)
    return 0 if result.get(_SUCCESS[args.action]) is True else 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse spend``."""
    root = subparsers.add_parser(
        "spend", help="Operate a shared-pool owner's spend ledger, or ask an owner hub."
    )
    commands = root.add_subparsers(dest="spend_command", required=True)
    for name, help_text, needs in (
        ("configure", "Append one owner-only pool configuration file.", "file"),
        ("reconcile", "Close a reservation with an evidenced amount.", "file"),
        ("status", "Print a pool's bound, charged, held amounts and headroom.", "pool"),
        ("audit", "Print every recorded event of a pool.", "pool"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--ledger", required=True, help="Owner-only spend ledger file.")
        command.add_argument("--hub-id", required=True, help="This owner hub's id.")
        if needs == "file":
            command.add_argument("--file", required=True, help="Owner-only JSON document.")
        else:
            command.add_argument("--pool", required=True, help="Pool id.")
        command.set_defaults(func=_operator)
    request = commands.add_parser("request", help="Send one peer request to an owner hub.")
    request.add_argument("action", choices=SPEND_ACTIONS)
    request.add_argument("--uri", required=True, help="The owner hub's websocket URI.")
    request.add_argument("--local-id", required=True, help="This hub's id, as the owner grants it.")
    request.add_argument("--file", required=True, help="Owner-only JSON request document.")
    request.add_argument("--token-file", help="Owner-only connect token for a secured owner.")
    request.add_argument("--peer-identity-key", help="This hub's identity key (PEM).")
    request.add_argument("--peer-identity-key-id", help="The key id the owner enrolled.")
    request.add_argument(
        "--timeout", type=float, default=DEFAULT_SPEND_TIMEOUT, help="Seconds to wait."
    )
    request.set_defaults(func=_request)
