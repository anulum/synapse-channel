# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal host-session observation
"""Render the same local metadata contract used by the dashboard."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import time
from pathlib import Path

from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.host_sessions import HostSessionMonitor


def format_runtime(seconds: float) -> str:
    """Format a non-negative duration as ``<days>d HH:MM:SS`` or ``HH:MM:SS`` with whole seconds.

    Parameters
    ----------
    seconds : float
        Finite non-negative duration in seconds; fractions are truncated.

    Returns
    -------
    str
        ``HH:MM:SS`` below one day, otherwise ``<days>d HH:MM:SS``.

    Raises
    ------
    ValueError
        If ``seconds`` is negative, non-finite or not a number.
    """
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("runtime must be a finite non-negative number of seconds")
    whole = int(seconds)
    days, remainder = divmod(whole, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {clock}" if days else clock


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _runtime(document: dict[str, object], row: dict[str, object]) -> str:
    started = _finite_number(row.get("started_at"))
    observed = _finite_number(document.get("observed_at"))
    if started is None or observed is None or started > observed:
        return "unknown"
    return format_runtime(observed - started)


def render_host_observation(document: dict[str, object]) -> str:
    """Render bounded JSON as plain terminal text, escaping control characters.

    Parameters
    ----------
    document : dict
        Version-checked observation from the collector or bounded HTTP feed.
        This renderer is not a complete wire-schema validator.

    Returns
    -------
    str
        Process rows and optional metadata with control characters escaped.
        Missing values appear as unknown, not as evidence of absence.

    Raises
    ------
    ValueError
        Rows are not dictionaries in a list of at most 256 entries.
    """
    lines = [
        f"HOST {document.get('host_ref')}  observation {document.get('observation_id')}",
        f"process scan: {document.get('process_status')} | tmux: {document.get('tmux_status')}",
        "PID  PROCESS  OS STATE  IDENTITY  TMUX",
    ]
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError("invalid host rows")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid host row")
        lines.append(
            "  ".join(
                str(row.get(key) if row.get(key) is not None else "unknown")
                for key in ("pid", "command_name", "state", "identity", "pane")
            )
        )
        lines.append(
            f"  runtime: {_runtime(document, row)} ({row.get('started_at_status', 'unknown')})"
        )
        for key in ("cwd", "context_id"):
            status_key = "cwd_status" if key == "cwd" else "context_status"
            lines.append(f"  {key}: {row.get(key) or 'unknown'} ({row.get(status_key, 'unknown')})")
    return "\n".join(
        "".join(ch if ch.isprintable() else f"\\u{ord(ch):04x}" for ch in line) for line in lines
    )


def _cmd_pid_monitor(args: argparse.Namespace) -> int:
    try:
        monitor = HostSessionMonitor(
            pids=tuple(args.pid), tmux_socket=args.tmux_socket, context_root=args.context_root
        )
        if args.dashboard_port is not None and (
            args.pid or args.tmux_socket or args.paths or args.context or args.context_root
        ):
            raise ValueError("connected mode uses server scope and grants, not local options")
        for sample in range(args.samples):
            if sample:
                time.sleep(2.0)
            if args.dashboard_port is None:
                raw = monitor.snapshot(paths=args.paths, context=args.context).to_json()
            else:
                if args.token_file is None:
                    raise ValueError("--dashboard-port requires --token-file")
                bearer = read_secret_file(args.token_file, flag="--token-file")
                connection = http.client.HTTPConnection("127.0.0.1", args.dashboard_port, timeout=3)
                try:
                    connection.request(
                        "GET", "/host-sessions.json", headers={"Authorization": f"Bearer {bearer}"}
                    )
                    response = connection.getresponse()
                    raw = response.read(1048577)
                    if response.status != 200 or len(raw) > 1048576:
                        raise ValueError(f"host feed unavailable (HTTP {response.status})")
                finally:
                    connection.close()
            document: object = json.loads(raw)
            if (
                not isinstance(document, dict)
                or type(document.get("version")) is not int
                or document["version"] != 1
            ):
                raise ValueError("incompatible host observation")
            print(
                json.dumps(document, ensure_ascii=True)
                if args.json
                else render_host_observation(document)
            )
    except (OSError, ValueError, http.client.HTTPException) as exc:
        print(f"host monitor: {type(exc).__name__}; observation unavailable")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


def _samples(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 3600:
        raise argparse.ArgumentTypeError("samples must be between 1 and 3600")
    return result


def _port(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise argparse.ArgumentTypeError("dashboard port must be between 1 and 65535")
    return result


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register read-only local and dashboard-connected monitor modes.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        CLI command registry receiving pid-monitor and its argument handler.
        Local consent flags cannot be combined with dashboard-connected mode.
    """
    parser = subparsers.add_parser("pid-monitor", help="Observe local process/session metadata.")
    parser.add_argument(
        "--pid", type=int, action="append", default=[], help="Explicit PID scope (repeatable)."
    )
    parser.add_argument("--tmux-socket", help="Explicit local tmux socket; never creates a server.")
    parser.add_argument(
        "--paths", action="store_true", help="Consent to cwd observation in local mode."
    )
    parser.add_argument(
        "--context", action="store_true", help="Consent to open Codex context pathname observation."
    )
    parser.add_argument(
        "--context-root", type=Path, help="Allowed context pathname root; still requires --context."
    )
    parser.add_argument(
        "--samples", type=_samples, default=1, help="1..3600 snapshots, two seconds apart."
    )
    parser.add_argument(
        "--dashboard-port", type=_port, help="Read the shared loopback HTTP observation instead."
    )
    parser.add_argument("--token-file", type=Path, help="Owner-only dashboard credential file.")
    parser.add_argument("--json", action="store_true", help="Print versioned JSON.")
    parser.set_defaults(func=_cmd_pid_monitor)
