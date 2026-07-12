# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTML rendering for the read-only dashboard page
"""HTML rendering for the read-only dashboard page.

Turning a :class:`~synapse_channel.dashboard.DashboardSnapshot` into an escaped
HTML page is one self-contained responsibility, kept out of the server module so
the HTTP handler does not carry the markup: every value that reaches a text node
goes through one escape helper, each snapshot section renders independently, and
the assembled fallback page is handed to the cockpit shell
(:func:`~synapse_channel.dashboard_cockpit.render_cockpit_html`).
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from synapse_channel.dashboard_cockpit import render_cockpit_html
from synapse_channel.dashboard_fleet import render_fleet_visibility_html

if TYPE_CHECKING:
    from synapse_channel.dashboard import DashboardSnapshot, ManifestCards, SnapshotMapping
    from synapse_channel.observed_peers import ObservedPeerSnapshot


def _escape(value: object) -> str:
    """Return ``value`` escaped for HTML text nodes."""
    return html.escape(str(value), quote=True)


def _render_list(items: list[str]) -> str:
    """Render escaped list items or a single empty marker."""
    if not items:
        return '<li class="muted">None</li>'
    return "".join(f"<li>{_escape(item)}</li>" for item in items)


def _render_claims(state: SnapshotMapping) -> str:
    """Render active claims from a state snapshot."""
    claims = state.get("active_claims", [])
    if not isinstance(claims, list) or not claims:
        return '<li class="muted">No active claims</li>'
    rows: list[str] = []
    sorted_claims = sorted(
        (claim for claim in claims if isinstance(claim, Mapping)),
        key=lambda claim: str(claim.get("task_id", "")),
    )
    for claim in sorted_claims:
        owner = _escape(claim.get("owner", "-"))
        paths = claim.get("paths", [])
        rendered_paths = (
            ", ".join(_escape(path) for path in paths) if isinstance(paths, list) else "-"
        )
        rows.append(
            f"<li><strong>{_escape(claim.get('task_id', '-'))}</strong> — "
            f"{owner}<br><small>{rendered_paths}</small></li>"
        )
    return "".join(rows)


def _render_tasks(board: SnapshotMapping) -> str:
    """Render task cards from a board snapshot."""
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        return '<li class="muted">No board tasks</li>'
    rows: list[str] = []
    for raw_task in tasks:
        if not isinstance(raw_task, Mapping):
            continue
        rows.append(
            "<li>"
            f"<strong>{_escape(raw_task.get('task_id', '-'))}</strong> "
            f"<span>{_escape(raw_task.get('status', '-'))}</span><br>"
            f"{_escape(raw_task.get('title', ''))}"
            "</li>"
        )
    return "".join(rows) if rows else '<li class="muted">No board tasks</li>'


def _render_progress(board: SnapshotMapping) -> str:
    """Render recent board progress notes."""
    progress = board.get("progress", [])
    if not isinstance(progress, list) or not progress:
        return '<li class="muted">No progress notes</li>'
    rows: list[str] = []
    for raw_note in progress[-10:]:
        if not isinstance(raw_note, Mapping):
            continue
        rows.append(
            "<li>"
            f"{_escape(raw_note.get('author', '-'))} "
            f"[{_escape(raw_note.get('kind', '-'))}] "
            f"{_escape(raw_note.get('task_id', '-'))}: "
            f"{_escape(raw_note.get('text', ''))}"
            "</li>"
        )
    return "".join(rows) if rows else '<li class="muted">No progress notes</li>'


def _render_manifest(manifest: ManifestCards) -> str:
    """Render advertised capability cards."""
    if not manifest:
        return '<li class="muted">No advertised capabilities</li>'
    rows: list[str] = []
    for card in manifest:
        classes = card.get("task_classes", [])
        class_text = (
            ", ".join(_escape(item) for item in classes) if isinstance(classes, list) else "-"
        )
        contracts = card.get("contracts", [])
        contract_text = (
            f" · contracts: {len(contracts)}" if isinstance(contracts, list) and contracts else ""
        )
        verification = card.get("verification", {})
        verification_text = (
            _escape(verification.get("result", "missing_signature"))
            if isinstance(verification, Mapping)
            else "missing_signature"
        )
        rows.append(
            "<li>"
            f"<strong>{_escape(card.get('agent', '-'))}</strong> "
            f"<small>{class_text}{contract_text} · signature: {verification_text}</small><br>"
            f"{_escape(card.get('description', ''))}"
            "</li>"
        )
    return "".join(rows)


def _render_observed_peer_rows(peers: tuple[ObservedPeerSnapshot, ...]) -> str:
    """Render advisory observed peer rows for the fallback dashboard HTML."""
    if not peers:
        return '<li class="muted">No observed peers configured</li>'
    rows: list[str] = []
    for peer in peers:
        label = f"observed@{peer.hub_id}"
        if not peer.reachable:
            rows.append(
                f"<li><strong>{_escape(label)}</strong> unreachable"
                f"<br><small>{_escape(peer.error or 'fetch failed')}</small></li>"
            )
            continue
        lag = "unknown" if peer.lag is None else str(peer.lag)
        agents = ", ".join(peer.observed_agents) or "no observed claim owners"
        rows.append(
            f"<li><strong>{_escape(label)}</strong> cursor={peer.cursor} "
            f"lag={_escape(lag)} claims={len(peer.state.observed_claims)}"
            f"<br><small>{_escape(agents)}</small></li>"
        )
    return "".join(rows)


def render_dashboard_html(
    snapshot: DashboardSnapshot,
    *,
    refresh_seconds: int = 5,
    a2a_state_file: str | Path | None = None,
) -> str:
    """Render a complete read-only HTML dashboard page.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        Read-side snapshot fetched from the hub.
    refresh_seconds : int, optional
        Browser refresh interval. Values below one are coerced to one second.
    a2a_state_file : str, pathlib.Path, or None, optional
        Optional persisted A2A bridge state file used to populate the fleet
        visibility section.

    Returns
    -------
    str
        Escaped HTML page.
    """
    refresh = max(1, int(refresh_seconds))
    ready = snapshot.board.get("ready", [])
    ready_items = [str(item) for item in ready] if isinstance(ready, list) else []
    fleet_html = render_fleet_visibility_html(snapshot, a2a_state_file=a2a_state_file)
    fallback_html = f"""<h1>SYNAPSE CHANNEL dashboard</h1>
  <div class="grid">
    <section>
      <h2>Online agents ({len(snapshot.online_agents)})</h2>
      <ul>{_render_list(snapshot.online_agents)}</ul>
    </section>
    <section><h2>Ready tasks</h2><ul>{_render_list(ready_items)}</ul></section>
    <section><h2>Active claims</h2><ul>{_render_claims(snapshot.state)}</ul></section>
    <section><h2>Board tasks</h2><ul>{_render_tasks(snapshot.board)}</ul></section>
    <section><h2>Recent progress</h2><ul>{_render_progress(snapshot.board)}</ul></section>
    <section><h2>Capability manifest</h2><ul>{_render_manifest(snapshot.manifest)}</ul></section>
    <section><h2>Observed peer hubs</h2>
      <ul>{_render_observed_peer_rows(snapshot.observed_peers)}</ul>
    </section>
    {fleet_html}
  </div>"""
    return render_cockpit_html(refresh_seconds=refresh, fallback_html=fallback_html)
