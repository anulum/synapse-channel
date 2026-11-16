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
import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from peer_hub import observed_peer_hub
from websockets.asyncio.client import connect

from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.attention import project_hub_attention
from synapse_channel.core.attention_store import sync_evidence
from synapse_channel.core.finding_records import Finding
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_finding, record_operator_relay
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.observed_peers import ObservedPeerSpec

HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 18765
DEFAULT_HUB_PORT = 18766
DASHBOARD_NAME = "cockpit-e2e-dashboard"
"""The dashboard's identity on the hub; it pulls an observed peer's log as ``<name>-observed``."""

SEEDED_FINDINGS = (
    {
        "statement": "cockpit-e2e verified finding",
        "subkind": "codebase-fact",
        "evidence_kind": "measured",
        "claim_status": "reference-validated",
        "evidence_ref": "clients/cockpit/e2e/dashboard_harness.py",
        "verified_at_source": {"checked_this_session": True, "source_ref": "dashboard_harness.py"},
        "provenance": {"project": "cockpit-e2e"},
    },
    {
        "statement": "cockpit-e2e retracted finding",
        "subkind": "estimate",
        "lifecycle": "retracted",
        "provenance": {"project": "cockpit-e2e"},
    },
)
"""Findings the browser gate expects in the panel: one re-checked, one withdrawn."""

OPERATIONS_APPROVER = "P/approver-cockpit"
"""The operator's relay identity when the harness journals operations.

The journalled operations are declared by ``P/worker``. The hub counts an
approval only from an identity of the declarer's project that is not the
declarer, and the dashboard relays only under a name no agent uses, so the
operations journey can record a decision that counts.
"""


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
            operations = os.environ.get("SYNAPSE_COCKPIT_E2E_OPERATIONS") == "1"
            relay = OPERATIONS_APPROVER if operations else "operator:cockpit-e2e"
            principal["operator_name"] = relay
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


def _attention_store(journal: EventStore, scratch: Path) -> Path:
    """Project the journal's attention evidence into the dashboard's store, and return it."""
    attention_store = scratch / "attention" / "queue.db"
    observed_at = time.time()
    sync_evidence(
        attention_store,
        source="hub",
        evidence=project_hub_attention(journal.iter_events(), now=observed_at),
        now=observed_at,
    )
    return attention_store


def _seed_journal(journal: EventStore, scratch: Path) -> Path:
    """Journal the gate's audit, approval and finding evidence, and return the attention store.

    Parameters
    ----------
    journal : EventStore
        The hub's journal, written before the hub serves it.
    scratch : pathlib.Path
        The harness's scratch folder, where the attention store is written.
    """
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
    journal.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "",
            "kind": "approval",
            "author": "operator:cockpit-e2e-seed",
            "posted_at": time.time() - 172800,
            "text": format_approval_note(
                subject="attention-e2e-task", state="requested", reason="private task body"
            ),
        },
        ts=time.time() - 172800,
        durable=True,
    )
    # The README picture journey turns this off: its panel shows only the
    # demonstration fleet's own finding.
    if os.environ.get("SYNAPSE_COCKPIT_E2E_SEED_FINDINGS", "1") == "1":
        for seeded in SEEDED_FINDINGS:
            # The record the hub journals: parsed, then stamped with an attested origin.
            finding = Finding.from_dict(seeded).attested(by="cockpit-e2e/reviewer", at=time.time())
            record_finding(journal, finding.as_dict())
    return _attention_store(journal, scratch)


async def _journal_operations(path: Path) -> None:
    """Journal one governed operation in every state the operation report gives.

    A real hub writes them through its own requests, with the writer the
    report's and the feed's tests share, so the operations journey reads the
    same store those tests read. The writer lives in the repository's ``tests``
    folder, which this harness puts on the import path only when asked.

    Parameters
    ----------
    path : pathlib.Path
        The event store the harness's hub then journals to; it must not exist yet.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
    await importlib.import_module("operation_report_store").journal_every_state_async(path)


def _scope_groups(scratch: Path) -> Path | None:
    """Write the owner-only groups file the scope journey declares, when it asks for one."""
    if os.environ.get("SYNAPSE_COCKPIT_E2E_SCOPE_GROUPS") != "1":
        return None
    path = scratch / "scope-groups.json"
    path.write_text(json.dumps({"version": 1, "groups": {"ships": ["harbour", "lighthouse"]}}))
    path.chmod(0o600)
    return path


def _dashboard(
    hub_uri: str,
    port: int,
    scratch: Path,
    dist: Path,
    observed: tuple[ObservedPeerSpec, ...],
    access_file: Path,
) -> DashboardServer:
    """Start the read-gated operator dashboard on the hub, observing ``observed``."""
    return start_dashboard_server(
        host=HOST,
        port=port,
        uri=hub_uri,
        name=DASHBOARD_NAME,
        token=None,
        ready_timeout=2.0,
        response_timeout=2.0,
        refresh_seconds=1,
        allow_non_loopback=False,
        dashboard_access_file=access_file,
        cockpit_dist=dist,
        reliability_db=scratch / "hub.db",
        attention_store=scratch / "attention" / "queue.db",
        scope_groups_file=_scope_groups(scratch),
        operator=True,
        observed_peers=observed,
        observed_timeout=2.0,
    )


async def _serve() -> None:
    """Run the real hub and read-gated operator dashboard until terminated."""
    dashboard_port = _port_from_env("SYNAPSE_COCKPIT_E2E_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT)
    hub_port = _port_from_env("SYNAPSE_COCKPIT_E2E_HUB_PORT", DEFAULT_HUB_PORT)
    tokens = {
        "viewer": os.environ.get("SYNAPSE_COCKPIT_E2E_VIEWER_TOKEN", ""),
        "operator": os.environ.get("SYNAPSE_COCKPIT_E2E_TOKEN", ""),
        "admin": os.environ.get("SYNAPSE_COCKPIT_E2E_ADMIN_TOKEN", ""),
    }
    dist = Path(
        os.environ.get(
            "SYNAPSE_COCKPIT_E2E_DIST", str(Path(__file__).resolve().parents[1] / "dist")
        )
    )
    if not (dist / "index.html").is_file():
        raise FileNotFoundError("build the cockpit before running the browser gate")

    scratch = TemporaryDirectory(prefix="synapse-cockpit-e2e-")
    scratch_path = Path(scratch.name)
    access_file = _access_policy(scratch_path, tokens)
    if os.environ.get("SYNAPSE_COCKPIT_E2E_OPERATIONS") == "1":
        await _journal_operations(scratch_path / "hub.db")
    journal = EventStore(scratch_path / "hub.db")
    _seed_journal(journal, scratch_path)
    hub = SynapseHub(hub_id="cockpit-e2e", journal=journal)
    hub_task = asyncio.create_task(hub.serve(HOST, hub_port))
    dashboard = None
    peers = contextlib.AsyncExitStack()
    try:
        hub_uri = f"ws://{HOST}:{hub_port}"
        await _await_hub(hub_uri)
        observer = f"{DASHBOARD_NAME}-observed"
        observed = await peers.enter_async_context(observed_peer_hub(scratch_path, HOST, observer))
        dashboard = _dashboard(hub_uri, dashboard_port, scratch_path, dist, observed, access_file)
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
        await peers.aclose()
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
