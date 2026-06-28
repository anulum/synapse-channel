# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only dashboard CLI command
"""Argparse registration and dispatcher for ``synapse dashboard``."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.dashboard import start_dashboard_server


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Run the read-only local dashboard until interrupted."""
    try:
        server = start_dashboard_server(
            host=args.host,
            port=args.port,
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
            response_timeout=args.response_timeout,
            refresh_seconds=args.refresh_seconds,
            allow_non_loopback=args.allow_non_loopback,
            a2a_state_file=args.a2a_state_file,
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    print(f"dashboard: {server.url('/')}")
    print("snapshot JSON: " + server.url("/snapshot.json"))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``dashboard`` subcommand."""
    dashboard = subparsers.add_parser(
        "dashboard",
        help="Serve a loopback-only read-only web dashboard for hub state.",
    )
    dashboard.add_argument("--uri", default=DEFAULT_HUB_URI)
    dashboard.add_argument("--name", default="DASHBOARD")
    dashboard.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    dashboard.add_argument("--port", type=int, default=8765, help="HTTP bind port.")
    dashboard.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Permit dashboard binds outside loopback; use only behind trusted controls.",
    )
    dashboard.add_argument(
        "--refresh-seconds",
        type=int,
        default=5,
        help="Browser refresh interval for the HTML page.",
    )
    dashboard.add_argument(
        "--response-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for read-side hub snapshots per page request.",
    )
    dashboard.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    dashboard.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    dashboard.add_argument(
        "--a2a-state-file",
        type=Path,
        default=None,
        help="Optional persisted A2A bridge state file summarised in the dashboard.",
    )
    dashboard.set_defaults(func=_cmd_dashboard)
