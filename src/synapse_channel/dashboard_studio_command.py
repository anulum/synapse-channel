# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio command-centre HTML shell and runtime configuration
"""Render the Studio command-centre shell.

The shell is hub-independent and contains no mutable coordination behaviour.
Focused package assets own the instrument layout, snapshot polling, durable-feed
polling, and safe board-column DOM rendering. The only inline script is a
server-authored, secret-free runtime configuration containing fixed feed paths
and the bounded poll interval.
"""

from __future__ import annotations

import json

from synapse_channel.dashboard_studio import STUDIO_REFERENCE_PATH
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH

STUDIO_COMMAND_PATH = "/studio/command"
"""HTTP path the live Studio command centre is served at."""

EVENTS_FEED_PATH = "/events.json"
"""HTTP path for the optional durable event-log tail feed."""

OPERATOR_ACTIONS_FEED_PATH = "/operator-actions.json"
"""HTTP path for the optional governed operator-action history feed."""

DEFAULT_POLL_SECONDS = 5
"""How often the command centre re-reads the live snapshot."""

STUDIO_COMMAND_STYLES = (
    "studio.css",
    "board-columns.css",
    "studio-command.css",
)
"""Fixed package stylesheets loaded by the command-centre shell."""

STUDIO_COMMAND_SCRIPTS = (
    "board-columns.js",
    "studio-feeds.js",
    "studio-command.js",
)
"""Fixed package scripts loaded in dependency order by the shell."""


def _runtime_config(*, poll_seconds: int) -> str:
    """Return the secret-free JavaScript runtime configuration."""
    payload = {
        "eventsUrl": EVENTS_FEED_PATH,
        "operatorActionsUrl": OPERATOR_ACTIONS_FEED_PATH,
        "pollMs": max(1, int(poll_seconds)) * 1000,
        "snapshotUrl": STUDIO_SNAPSHOT_PATH,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"window.__SYN_STUDIO__ = Object.freeze({encoded});"


def render_studio_command_html(*, poll_seconds: int = DEFAULT_POLL_SECONDS) -> str:
    """Render the offline-safe, read-only Studio command-centre page.

    Parameters
    ----------
    poll_seconds : int, optional
        Snapshot and optional-feed refresh interval, floored at one second.

    Returns
    -------
    str
        Complete HTML shell linking only fixed package assets.
    """
    styles = "\n".join(
        f'  <link rel="stylesheet" href="/{asset}">' for asset in STUDIO_COMMAND_STYLES
    )
    scripts = "\n".join(f'  <script src="/{asset}"></script>' for asset in STUDIO_COMMAND_SCRIPTS)
    runtime_config = _runtime_config(poll_seconds=poll_seconds)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SYNAPSE Studio — command centre</title>
{styles}
</head>
<body class="syn">
  <div class="cc-shell">
    <aside class="cc-rail" aria-label="Studio navigation">
      <div class="cc-rail-title">SYNAPSE Studio</div>
      <nav class="syn-nav">
        <a href="{STUDIO_COMMAND_PATH}" aria-current="page">command</a>
        <a href="{STUDIO_REFERENCE_PATH}">design</a>
        <a href="#cc-board-columns">board</a>
        <a href="#cc-agents-list">fleet</a>
        <a href="#cc-livefeed-list">live feed</a>
        <a href="#cc-actions-list">operator actions</a>
        <a href="#cc-posture-list">security</a>
        <a href="#cc-peers-list">peers</a>
      </nav>
    </aside>
    <main class="cc-main">
      <header class="cc-header">
        <div class="cc-title">
          <h1>Coordination command centre</h1>
          <span id="cc-offline" class="cc-offline" hidden>connecting…</span>
        </div>
        <div class="cc-headerbar" aria-label="Hub status">
          <span id="cc-connection" class="syn-verdict syn-verdict--amber">connecting</span>
          <span id="cc-verdict" class="syn-verdict syn-verdict--amber">unknown</span>
          <div class="cc-chip"><span>hub</span><b id="cc-hub">unknown</b></div>
          <div class="cc-chip"><span>version</span><b id="cc-version">unknown</b></div>
        </div>
      </header>
      <div class="cc-bar">
        <div class="cc-stat"><b id="cc-agents">0</b><span>live agents</span></div>
        <div class="cc-stat"><b id="cc-claims">0 / 0</b><span>claims active/stale</span></div>
        <div class="cc-stat"><b id="cc-tasks">0 / 0</b><span>tasks ready/blocked</span></div>
        <div class="cc-stat"><b id="cc-conflicts">0</b><span>conflicts</span></div>
        <div class="cc-stat"><b id="cc-signals">0</b><span>risk signals</span></div>
        <div class="cc-stat"><b id="cc-posture">unknown</b><span>security posture</span></div>
        <div class="cc-stat"><b id="cc-peers">—</b><span>peers reachable</span></div>
      </div>
      <section class="syn-panel cc-board-panel" aria-labelledby="cc-board-title">
        <div id="cc-board-title" class="syn-label">shared plan · exact board and claim states</div>
        <p class="cc-board-boundary">Read-only projection; actions remain hub-enforced.</p>
        <div id="cc-board-columns" class="syn-board-columns" aria-live="polite">
          <div class="syn-board-empty">Waiting for snapshot</div>
        </div>
      </section>
      <div class="cc-grid">
        <section class="syn-panel cc-clock-wrap">
          <div class="syn-label">coordination clock</div>
          <svg id="cc-clock" class="cc-clock" viewBox="0 0 360 360"
            role="img" aria-label="Coordination clock: claims by lease health"></svg>
          <div class="cc-fallback">
            <table class="cc-table">
              <thead><tr><th>owner</th><th>scope</th><th>state</th></tr></thead>
              <tbody id="cc-fallback-body"></tbody>
            </table>
          </div>
        </section>
        <div class="cc-stack">
          <section class="syn-panel">
            <div class="syn-label">agents</div>
            <div id="cc-agents-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">claims</div>
            <div id="cc-claims-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">tasks</div>
            <div id="cc-tasks-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">risk signals</div>
            <div id="cc-risk-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">security posture</div>
            <div id="cc-posture-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">observed peers (advisory)</div>
            <div id="cc-peers-list" class="cc-panel-list"></div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">live feed</div>
            <div id="cc-livefeed-list" class="cc-feed cc-panel-list">
              <div class="cc-empty">connecting to event feed</div>
            </div>
          </section>
          <section class="syn-panel">
            <div class="syn-label">operator actions</div>
            <div id="cc-actions-list" class="cc-feed cc-panel-list">
              <div class="cc-empty">connecting to operator-actions feed</div>
            </div>
          </section>
        </div>
      </div>
    </main>
  </div>
  <script>{runtime_config}</script>
{scripts}
</body>
</html>
"""
