# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private HTTPS MCP command profile
"""Run authenticated Streamable HTTP with explicit private transport authority."""

from __future__ import annotations

import argparse
import ipaddress
import ssl
import sys
from typing import Literal

from synapse_channel.core.secret_files import SecretFileError, read_secret_file


def add_http_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the opt-in private HTTPS profile on the existing MCP command."""
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--project", help="Fixed project for provisioned HTTP subjects.")
    parser.add_argument("--http-auth-file", help="Owner-only issuer and seat-grant JSON file.")
    parser.add_argument("--tls-cert-file", help="PEM certificate chain for direct HTTPS.")
    parser.add_argument("--tls-key-file", help="Owner-only unencrypted PEM TLS key.")
    parser.add_argument(
        "--http-host", default="127.0.0.1", help="Loopback IP only; use a private tunnel."
    )
    parser.add_argument("--http-port", type=int, default=8888)
    parser.add_argument(
        "--http-allowed-host", action="append", help="Explicit HTTP Host allowlist."
    )
    parser.add_argument(
        "--http-allowed-origin", action="append", help="Explicit browser Origin allowlist."
    )
    parser.add_argument("--http-max-requests", type=int, default=32)
    parser.add_argument("--http-max-sessions", type=int, default=8)
    parser.add_argument("--http-request-bytes", type=int, default=65536)
    parser.add_argument("--http-reply-bytes", type=int, default=262144)


def run_http(args: argparse.Namespace) -> int:
    """Serve the bounded TLS app using provisioned seats, without ambient identity.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed MCP command arguments. HTTP requires explicit project, grants,
        certificate, owner-only key and Host allowlist. Native hub credentials
        may only come from ``--token-file``.

    Returns
    -------
    int
        Zero after orderly shutdown, two for an invalid profile, or one when
        dependencies, TLS, native admission or listener startup fail.

    Notes
    -----
    Uvicorn drains the application before replaying termination signals. SIGINT
    is caught here; SIGTERM retains the operating system's signal exit status.
    """
    try:
        if (
            not args.project
            or not args.http_auth_file
            or not args.tls_cert_file
            or not args.tls_key_file
            or not args.http_allowed_host
            or not 1 <= args.http_port <= 65535
            or not ipaddress.ip_address(args.http_host).is_loopback
            or args.token is not None
            or args.name is not None
            or args.role
            or args.inbox_feed is not None
            or args.inbox_cursor is not None
            or any(value == "*" for value in args.http_allowed_host)
            or any("*" in value for value in (args.http_allowed_origin or []))
        ):
            raise ValueError("invalid private HTTPS profile")
    except ValueError:
        print("synapse mcp: invalid private HTTPS profile; see --help", file=sys.stderr)
        return 2
    try:
        import uvicorn
        from pydantic import TypeAdapter

        from synapse_channel.mcp.http_application import build_http_mcp_app
    except ImportError:
        print("synapse mcp: install synapse-channel[mcp] for HTTPS", file=sys.stderr)
        return 1
    try:
        read_secret_file(args.tls_key_file, flag="--tls-key-file", require_single_link=True)
        token = (
            read_secret_file(args.token_file, flag="--token-file", require_single_link=True)
            if args.token_file is not None
            else None
        )
        app = build_http_mcp_app(
            auth_file=args.http_auth_file,
            project=args.project,
            hub_uri=args.uri,
            hub_token=token,
            allowed_hosts=args.http_allowed_host,
            allowed_origins=args.http_allowed_origin or [],
            max_requests=args.http_max_requests,
            max_sessions=args.http_max_sessions,
            request_bytes=args.http_request_bytes,
            reply_bytes=args.http_reply_bytes,
            operation_timeout=args.request_timeout,
            ready_timeout=args.ready_timeout,
        )
        config = uvicorn.Config(
            app,
            host=args.http_host,
            port=args.http_port,
            ssl_certfile=args.tls_cert_file,
            ssl_keyfile=args.tls_key_file,
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
            proxy_headers=False,
            ws="none",
            lifespan="on",
            log_config=None,
            log_level="critical",
            access_log=False,
            server_header=False,
            limit_concurrency=args.http_max_requests + 1,
            backlog=128,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=5,
            h11_max_incomplete_event_size=args.http_request_bytes,
        )
        config.load()
        server = uvicorn.Server(config)
        server.run()
        TypeAdapter(Literal[True]).validate_python(server.started, strict=True)
        return 0
    except (ValueError, OSError, RuntimeError, SecretFileError, SystemExit):
        pass
    except KeyboardInterrupt:
        return 0
    print("synapse mcp: HTTPS startup or operation failed", file=sys.stderr)
    return 1
