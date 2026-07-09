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

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.dashboard import start_dashboard_server
from synapse_channel.observed_peers import parse_observed_peer


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
            dashboard_token=args.dashboard_token,
            reliability_db=args.reliability_db,
            federation_store=args.federation_store,
            cockpit_dist=args.cockpit_dist,
            operator=args.operator,
            operator_name=args.operator_name,
            observed_peers=tuple(args.observed_peers),
            observed_token=args.observed_token,
            observed_timeout=args.observed_timeout,
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    print(f"dashboard: {server.url('/')}")
    print("snapshot JSON: " + server.url("/snapshot.json"))
    if args.operator:
        print("operator write: POST " + server.url("/message"))
        print("operator task: POST " + server.url("/task"))
        print("operator task update: POST " + server.url("/task/update"))
    if args.reliability_db is not None:
        print("reliability JSON: " + server.url("/reliability.json"))
        print("events tail JSON: " + server.url("/events.json"))
        print("causality JSON: " + server.url("/causality.json"))
        print("receipts JSON: " + server.url("/receipts.json"))
    if args.federation_store is not None:
        print("federation JSON: " + server.url("/federation.json"))
    if args.cockpit_dist is not None:
        print("cockpit: " + server.url("/cockpit/"))
    if server.dashboard_token_generated and server.dashboard_token is not None:
        print("dashboard token: " + server.dashboard_token)
        print("dashboard auth: Authorization: Bearer <dashboard token>")
    elif server.dashboard_token is not None:
        print("dashboard auth: Authorization: Bearer <dashboard token>")
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
    dashboard.add_argument("--uri", default=default_hub_uri())
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
        "--dashboard-token",
        default=None,
        help=(
            "Bearer token required by dashboard HTTP requests; generated automatically "
            "for non-loopback binds when omitted."
        ),
    )
    dashboard.add_argument(
        "--a2a-state-file",
        type=Path,
        default=None,
        help="Optional persisted A2A bridge state file summarised in the dashboard.",
    )
    dashboard.add_argument(
        "--feeds-db",
        "--reliability-db",
        dest="reliability_db",
        type=Path,
        default=None,
        help=(
            "Hub event store powering the store-backed feeds: /reliability.json "
            "(audit signals, not scores), /events.json (raw log tail past a "
            "cursor), /receipts.json (universal receipt projections), and "
            "/causality.json (one causality query in the CLI's JSON shape). "
            "Read-only; without it each endpoint reports its absence with 404. "
            "--reliability-db is the same flag's original name."
        ),
    )
    dashboard.add_argument(
        "--federation-store",
        type=Path,
        default=None,
        help=(
            "Operator federation store powering /federation.json — imported "
            "peerings with provenance and bundle fingerprints; namespace "
            "outcomes are hub-runtime state and are not served."
        ),
    )
    dashboard.add_argument(
        "--cockpit-dist",
        type=Path,
        default=None,
        help=(
            "Built cockpit directory (clients/cockpit/dist) served read-only "
            "under /cockpit/; paths escaping the directory or with "
            "unrecognised suffixes are refused."
        ),
    )
    dashboard.add_argument(
        "--operator",
        action="store_true",
        help=(
            "Arm the operator write-path (POST /message, /task, /task/update) so the "
            "cockpit can relay chat and delegate board tasks to the fleet. Off by "
            "default: without it every write is a 404 and the dashboard stays a "
            "read-only observer. Writes still require the dashboard bearer token and "
            "are authorised and audited by the hub."
        ),
    )
    dashboard.add_argument(
        "--operator-name",
        default=None,
        help=(
            "Sender identity for relayed operator writes; 'operator:<name>' when "
            "omitted, so operator actions are attributed and never impersonate an agent."
        ),
    )
    dashboard.add_argument(
        "--observed-peer",
        action="append",
        default=[],
        type=parse_observed_peer,
        dest="observed_peers",
        metavar="HUB=URI",
        help=(
            "Fetch a peer hub's multi-hub event log and include observed@HUB "
            "advisory rows in dashboard snapshots. Repeat for multiple peers."
        ),
    )
    dashboard.add_argument(
        "--observed-token",
        default=None,
        help="Shared-secret token used for every --observed-peer pull.",
    )
    dashboard.add_argument(
        "--observed-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for each observed peer pull.",
    )
    dashboard.set_defaults(func=_cmd_dashboard)
