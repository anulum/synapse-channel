# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a2a-interop-trace CLI
"""CLI for independent-client A2A interoperability traces."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.a2a_credentials import A2APlaintextBearerError, resolve_a2a_token
from synapse_channel.a2a_interop_trace import (
    A2AInteropTraceError,
    parse_endpoint,
    run_local_interop_trace,
    write_interop_receipt,
)
from synapse_channel.a2a_outbound_response import A2AReceiptWriteError
from synapse_channel.core.secret_files import SecretFileError


def _cmd_a2a_interop_trace(args: argparse.Namespace) -> int:
    """Run discovery + task lifecycle against a live bridge; print or write receipt."""
    try:
        token = resolve_a2a_token(
            getattr(args, "a2a_token", None),
            getattr(args, "a2a_token_file", None),
        )
        if args.endpoint_url:
            scheme, host, port, prefix = parse_endpoint(args.endpoint_url)
        else:
            scheme, host, port, prefix = "http", args.host, int(args.port), ""
        if getattr(args, "scheme", None):
            scheme = str(args.scheme)
        receipt = run_local_interop_trace(
            host=host,
            port=port,
            path_prefix=prefix,
            token=token,
            message_text=args.message,
            timeout=float(args.timeout),
            scheme=scheme,
            ca_file=getattr(args, "ca_file", None),
            tls_insecure=bool(getattr(args, "tls_insecure", False)),
            allow_insecure_http=bool(getattr(args, "a2a_allow_insecure_http", False)),
        )
    except (SecretFileError, A2APlaintextBearerError) as exc:
        print(f"a2a-interop-trace: {exc}", file=sys.stderr)
        return 2
    except (ValueError, A2AInteropTraceError, OSError) as exc:
        print(f"a2a-interop-trace: {exc}", file=sys.stderr)
        return 1
    if args.output:
        try:
            written = write_interop_receipt(args.output, receipt)
        except A2AReceiptWriteError as exc:
            print(f"a2a-interop-trace: {exc}", file=sys.stderr)
            return 1
        print(f"wrote interop receipt: {written}")
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``a2a-interop-trace``."""
    cmd = subparsers.add_parser(
        "a2a-interop-trace",
        help=(
            "Run an independent stdlib HTTP(S) client against a live a2a-serve bridge "
            "and emit a discovery + task-lifecycle interop receipt."
        ),
    )
    cmd.add_argument(
        "--endpoint-url",
        default=None,
        help="Absolute http:// or https:// URL of the bridge (overrides --host/--port).",
    )
    cmd.add_argument("--host", default="127.0.0.1", help="Bridge host (default 127.0.0.1).")
    cmd.add_argument("--port", type=int, default=8877, help="Bridge port (default 8877).")
    cmd.add_argument(
        "--scheme",
        default=None,
        choices=("http", "https"),
        help="Override URL scheme when --host/--port are used (default http).",
    )
    cmd.add_argument(
        "--ca-file",
        default=None,
        help="PEM CA/trust file for HTTPS verification of the bridge certificate.",
    )
    cmd.add_argument(
        "--tls-insecure",
        action="store_true",
        help="Skip HTTPS certificate verification (local self-signed drills only).",
    )
    cmd.add_argument(
        "--a2a-token",
        default=None,
        help=(
            "Bearer token when the bridge protects message/task routes "
            "(process-visible; prefer the file form)."
        ),
    )
    cmd.add_argument(
        "--a2a-token-file",
        default=None,
        metavar="PATH",
        help=(
            "Read the A2A bearer from this owner-only file. An explicit "
            "--a2a-token wins without opening the file."
        ),
    )
    cmd.add_argument(
        "--a2a-allow-insecure-http",
        action="store_true",
        help=(
            "Explicitly allow a bearer over plaintext HTTP outside a literal "
            "loopback IP; accepts cleartext credential risk."
        ),
    )
    cmd.add_argument(
        "--message",
        default="synapse interop probe",
        help="Text part sent via POST /message:send.",
    )
    cmd.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-request timeout in seconds.",
    )
    cmd.add_argument(
        "--output",
        default=None,
        help="Write the receipt JSON to this path (stdout when omitted).",
    )
    cmd.set_defaults(func=_cmd_a2a_interop_trace)
