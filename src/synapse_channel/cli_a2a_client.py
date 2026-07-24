# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound a2a-client CLI
"""CLI for the outbound A2A HTTP(S) client against a second peer."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.a2a_client import A2AClientError, A2AOutboundClient


def _cmd_a2a_client(args: argparse.Namespace) -> int:
    """Discover, send, and get against ``--endpoint-url``."""
    try:
        client = A2AOutboundClient(
            args.endpoint_url,
            token=args.a2a_token,
            timeout=float(args.timeout),
            ca_file=args.ca_file,
            tls_insecure=bool(args.tls_insecure),
        )
        receipt = client.discover_send_get(args.message)
    except (ValueError, A2AClientError, OSError) as exc:
        print(f"a2a-client: {exc}", file=sys.stderr)
        return 1
    if args.output:
        path = args.output
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
        print(f"wrote outbound receipt: {path}")
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True))
    if not receipt.get("task_id") and "message" not in (receipt.get("send_response") or {}):
        return 1
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``a2a-client``."""
    cmd = subparsers.add_parser(
        "a2a-client",
        help=(
            "Outbound A2A HTTP(S) client: discover Agent Card, message:send, and GET task "
            "against a second peer endpoint."
        ),
    )
    cmd.add_argument(
        "--endpoint-url",
        required=True,
        help="Absolute http:// or https:// URL of the peer A2A bridge.",
    )
    cmd.add_argument("--a2a-token", default=None, help="Bearer token when the peer requires auth.")
    cmd.add_argument(
        "--message",
        default="synapse outbound a2a probe",
        help="Text part sent via POST /message:send.",
    )
    cmd.add_argument("--timeout", type=float, default=10.0)
    cmd.add_argument("--ca-file", default=None, help="PEM CA for HTTPS peer verification.")
    cmd.add_argument(
        "--tls-insecure",
        action="store_true",
        help="Skip HTTPS certificate verification (local self-signed only).",
    )
    cmd.add_argument("--output", default=None, help="Write receipt JSON to this path.")
    cmd.set_defaults(func=_cmd_a2a_client)
