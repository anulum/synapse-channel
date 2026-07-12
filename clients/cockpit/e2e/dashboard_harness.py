# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub and dashboard harness for the built cockpit
"""Serve the production cockpit against a real in-process Synapse hub."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

from websockets.asyncio.client import connect

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_operator_relay
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import start_dashboard_server

HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 18765
DEFAULT_HUB_PORT = 18766


def _port_from_env(name: str, default: int) -> int:
    """Return one validated TCP port from the environment.

    Parameters
    ----------
    name : str
        Environment variable to read.
    default : int
        Port used when the variable is absent.

    Returns
    -------
    int
        A port in the inclusive range 1..65535.

    Raises
    ------
    ValueError
        If the value is not a valid TCP port.
    """
    raw = os.environ.get(name, str(default))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer TCP port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


async def _await_hub(uri: str, timeout: float = 5.0) -> None:
    """Wait until the loopback hub completes a WebSocket handshake."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            async with connect(uri, open_timeout=0.5):
                return
        except (OSError, TimeoutError):
            await asyncio.sleep(0.02)
    raise TimeoutError(f"hub did not listen at {uri}")


def _access_policy(root: Path, tokens: dict[str, str]) -> Path:
    """Write a private three-principal policy used only by this harness."""
    principals: list[dict[str, str]] = []
    for role in ("viewer", "operator", "admin"):
        token = tokens[role]
        if len(token.encode("utf-8")) < 32:
            raise ValueError(f"SYNAPSE_COCKPIT_E2E_{role.upper()}_TOKEN must be at least 32 bytes")
        token_path = root / f"{role}.token"
        token_path.write_text(token, encoding="utf-8")
        token_path.chmod(0o600)
        principal = {"id": role, "role": role, "token_file": token_path.name}
        if role == "operator":
            principal["operator_name"] = "operator:cockpit-e2e"
        elif role == "admin":
            principal["operator_name"] = "operator:cockpit-e2e-admin"
        principals.append(principal)
    policy_path = root / "dashboard-access.json"
    policy_path.write_text(
        json.dumps({"version": 1, "principals": principals}),
        encoding="utf-8",
    )
    policy_path.chmod(0o600)
    return policy_path


async def _serve() -> None:
    """Run the real hub and read-gated operator dashboard until terminated."""
    dashboard_port = _port_from_env("SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT)
    hub_port = _port_from_env("SYNAPSE_COCKPIT_E2E_HUB_PORT", DEFAULT_HUB_PORT)
    tokens = {
        "viewer": os.environ.get("SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN", ""),
        "operator": os.environ.get("SYNAPSE_COCKPIT_E2E_TOKEN", ""),
        "admin": os.environ.get("SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN", ""),
    }
    dist = Path(__file__).resolve().parents[1] / "dist"
    if not (dist / "index.html").is_file():
        raise FileNotFoundError("build the cockpit before running the browser gate")

    scratch = TemporaryDirectory(prefix="synapse-cockpit-e2e-")
    access_file = _access_policy(Path(scratch.name), tokens)
    event_db = Path(scratch.name) / "hub.db"
    journal = EventStore(event_db)
    record_operator_relay(
        journal,
        {
            "action": "release",
            "direction": "out",
            "status": "applied",
            "applied": True,
            "pending": False,
            "namespace": "cockpit-e2e",
            "task_id": "cockpit-e2e-audit-seed",
            "operator": "operator:cockpit-e2e-seed",
            "detail": "seeded production audit evidence",
        },
    )
    hub = SynapseHub(hub_id="cockpit-e2e", journal=journal)
    hub_task = asyncio.create_task(hub.serve(HOST, hub_port))
    dashboard = None
    try:
        hub_uri = f"ws://{HOST}:{hub_port}"
        await _await_hub(hub_uri)
        dashboard = start_dashboard_server(
            host=HOST,
            port=dashboard_port,
            uri=hub_uri,
            name="cockpit-e2e-dashboard",
            token=None,
            ready_timeout=2.0,
            response_timeout=2.0,
            refresh_seconds=1,
            allow_non_loopback=False,
            dashboard_access_file=access_file,
            cockpit_dist=dist,
            reliability_db=event_db,
            operator=True,
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(signum, stop.set)
        print(f"cockpit e2e ready at {dashboard.url('/cockpit/')}", flush=True)
        await stop.wait()
    finally:
        if dashboard is not None:
            dashboard.close()
        hub_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hub_task
        journal.close()
        scratch.cleanup()


def main() -> int:
    """Run the browser harness and return its process exit code."""
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
