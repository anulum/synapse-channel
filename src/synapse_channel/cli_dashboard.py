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

from synapse_channel.cli_dashboard_access import add_dashboard_access_arguments
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_bearer_file import write_generated_bearer
from synapse_channel.dashboard_feed_serving import (
    OPTIONAL_SURFACES,
    configured_flags,
    packaged_cockpit_dist,
)
from synapse_channel.observed_peers import (
    parse_observed_peer,
    parse_observed_pin,
    resolve_observed_pins,
)

_STORE_FEED_LINES: tuple[tuple[str, str], ...] = (
    ("reliability JSON", "/reliability.json"),
    ("events tail JSON", "/events.json"),
    ("causality JSON", "/causality.json"),
    ("receipts JSON", "/receipts.json"),
    ("operator actions JSON", "/operator-actions.json"),
    ("sessions JSON", "/sessions.json"),
    ("findings JSON", "/findings.json"),
)
"""The event-store feeds announced at start when ``--feeds-db`` is given."""


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the local dashboard with optional explicitly armed operator relays.

    The cockpit build the package carries is the front door unless
    ``--cockpit-dist`` names another build; ``/`` then opens it.
    """
    cockpit_dist = args.cockpit_dist if args.cockpit_dist is not None else packaged_cockpit_dist()
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
            dashboard_access_file=args.dashboard_access_file,
            host_sessions_access_file=getattr(args, "host_sessions_access_file", None),
            fleet_observed_file=getattr(args, "fleet_observed_file", None),
            fleet_observed_access_file=getattr(args, "fleet_observed_access_file", None),
            host_session_context_root=getattr(args, "host_sessions_context_root", None),
            reliability_db=args.reliability_db,
            reliability_db_key_file=getattr(args, "feeds_db_key_file", None),
            attention_store=args.attention_store,
            federation_store=args.federation_store,
            cockpit_dist=cockpit_dist,
            operator=args.operator,
            operator_name=args.operator_name,
            observed_peers=tuple(args.observed_peers),
            observed_token=args.observed_token,
            observed_timeout=args.observed_timeout,
            observed_pins=resolve_observed_pins(
                getattr(args, "observed_pins", ()), tuple(args.observed_peers)
            ),
            allow_hosts=tuple(args.dashboard_allow_host),
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    if not _announce(args, server, cockpit_dist):
        server.close()
        return 2
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()


def _announce(args: argparse.Namespace, server: DashboardServer, cockpit_dist: Path | None) -> bool:
    """Print where everything is served and where the bearer is, never the bearer itself.

    A generated bearer is first written owner-only (:func:`write_generated_bearer`),
    and only its path is printed. Returns ``False``, having printed why, when it
    cannot be kept safely; the caller then closes the server.
    """
    bearer: Path | None = None
    if server.dashboard_token_generated and server.dashboard_token is not None:
        try:
            bearer = write_generated_bearer(server.dashboard_token)
        except (OSError, ValueError) as exc:  # SecurePathError is a ValueError
            print(f"refused: the generated dashboard bearer cannot be kept: {exc}")
            return False
    if cockpit_dist is not None:
        print(f"front door (cockpit): {server.url('/')}")
    else:
        print(f"studio (command centre): {server.url('/')}")
    print("studio command: " + server.url("/studio/command"))
    print("studio design reference: " + server.url("/studio"))
    print("classic hub HTML: " + server.url("/classic"))
    print("snapshot JSON: " + server.url("/snapshot.json"))
    print("studio JSON: " + server.url("/studio.json"))
    if args.operator:
        print("operator write: POST " + server.url("/message"))
        print("operator task: POST " + server.url("/task"))
        print("operator task update: POST " + server.url("/task/update"))
    if args.reliability_db is not None:
        for label, path in _STORE_FEED_LINES:
            print(f"{label}: {server.url(path)}")
    if args.attention_store is not None:
        print("attention JSON: " + server.url("/attention.json"))
    if args.federation_store is not None:
        print("federation JSON: " + server.url("/federation.json"))
    print("capabilities JSON: " + server.url("/capabilities.json"))
    given = configured_flags(
        feeds_db=args.reliability_db,
        federation_store=args.federation_store,
        attention_store=args.attention_store,
    )
    missing: dict[str, list[str]] = {}
    for surface in OPTIONAL_SURFACES:
        if surface.flag not in given:
            missing.setdefault(surface.flag, []).append(surface.name)
    for flag, names in missing.items():
        print(f"not served without {flag}: {', '.join(names)}")
    if args.dashboard_access_file is not None:
        print(f"dashboard access policy: {args.dashboard_access_file}")
    if bearer is not None:
        print(f"dashboard bearer: {bearer} (written for this start; a restart writes a new one)")
        print("dashboard auth: Authorization: Bearer <the token in that file>")
    elif server.dashboard_token is not None:
        print("dashboard auth: Authorization: Bearer <dashboard token>")
    return True


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``dashboard`` subcommand."""
    dashboard = subparsers.add_parser(
        "dashboard",
        help="Serve a local dashboard, read-only by default with opt-in operator relays.",
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
        "--dashboard-allow-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help=(
            "Extra Host authority admitted by the DNS-rebinding boundary, for a "
            "deliberate LAN or reverse-proxy exposure (repeatable). Loopback names "
            "and a concrete bind host are always admitted; bracket an IPv6 literal. "
            "A wildcard 0.0.0.0/:: bind relies on its read token instead, unless "
            "hosts are listed here to re-enable strict filtering."
        ),
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
    add_dashboard_access_arguments(dashboard)
    dashboard.add_argument(
        "--fleet-observed-file", type=Path, help="Opt-in versioned Fleet mirror export (read-only)."
    )
    dashboard.add_argument(
        "--fleet-observed-access-file", type=Path, help="Owner-only explicit Fleet observer grants."
    )
    dashboard.add_argument(
        "--host-sessions-access-file",
        type=Path,
        default=None,
        help="Opt-in owner-only host metadata grants, reloaded on each request.",
    )
    dashboard.add_argument(
        "--host-sessions-context-root",
        type=Path,
        help="Allowed context pathname root; does not grant context disclosure.",
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
            "Hub event store powering 13 read-only feeds, including "
            "/reliability.json (audit signals, not scores), /events.json (raw log "
            "tail), /postmortem.json?task=ID (replayable task evidence), "
            "/receipts.json, /causality.json, /operator-actions.json, "
            "/sessions.json and /findings.json (recorded findings, newest first). "
            "Read-only; without it each endpoint reports its absence with 404. "
            "--reliability-db is the same flag's original name."
        ),
    )
    dashboard.add_argument(
        "--feeds-db-key-file",
        dest="feeds_db_key_file",
        type=Path,
        default=None,
        help=(
            "Owner-only SQLCipher key for --feeds-db when the hub event store is "
            "encrypted (same material as synapse hub --db-key-file)."
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
        "--attention-store",
        type=Path,
        default=None,
        help="Explicit owner-local attention queue for authenticated /attention.json.",
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
        "--observed-pin",
        action="append",
        default=[],
        type=parse_observed_pin,
        dest="observed_pins",
        metavar="HUB=sha256:HEX",
        help=(
            "Accept the named observed peer's wss:// certificate only by this SHA-256 "
            "pin (repeatable). The hub must also be named by --observed-peer."
        ),
    )
    dashboard.add_argument(
        "--observed-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for each observed peer pull.",
    )
    dashboard.set_defaults(func=_cmd_dashboard)
