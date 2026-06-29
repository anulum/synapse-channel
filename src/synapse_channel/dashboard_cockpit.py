# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet nerve-center cockpit shell and static assets
"""Render the live cockpit shell and serve its static assets.

The cockpit is a dependency-free single-page client: the HTML shell links one
stylesheet and one script, both shipped as package data under
``dashboard_assets/``. The script polls ``/snapshot.json`` and renders the live
HUD, fleet graph, board lanes, claims, progress stream, receipts, and capability
manifest in place. A ``<noscript>`` block carries the server-rendered fallback so
the page is still informative without JavaScript and so static assertions keep
working. This module owns only presentation: it never reaches the hub.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

_ASSETS_DIR: Final = Path(__file__).parent / "dashboard_assets"

COCKPIT_ASSETS: Final[dict[str, str]] = {
    "cockpit.css": "text/css",
    "cockpit.js": "text/javascript",
}
"""Servable cockpit asset file names mapped to their content types."""


@lru_cache(maxsize=len(COCKPIT_ASSETS))
def load_cockpit_asset(name: str) -> str:
    """Return the text of a named cockpit asset.

    Parameters
    ----------
    name : str
        Asset file name; must be a key of :data:`COCKPIT_ASSETS`.

    Returns
    -------
    str
        UTF-8 asset contents.

    Raises
    ------
    KeyError
        If ``name`` is not an allowlisted cockpit asset.
    """
    if name not in COCKPIT_ASSETS:
        raise KeyError(name)
    return (_ASSETS_DIR / name).read_text(encoding="utf-8")


def _panel(title: str, body_id: str, *, count_id: str | None = None, scroll: bool = False) -> str:
    """Return one panel shell with an empty, JS-populated body."""
    count = f'<span class="panel__count" id="{count_id}">0</span>' if count_id else ""
    body_class = "panel__body panel__body--scroll" if scroll else "panel__body"
    return (
        f'<section class="panel"><div class="panel__head"><h2>{title}</h2>{count}</div>'
        f'<div class="{body_class}" id="{body_id}"></div></section>'
    )


def render_cockpit_html(*, refresh_seconds: int, fallback_html: str) -> str:
    """Render the cockpit single-page shell.

    Parameters
    ----------
    refresh_seconds : int
        Live poll interval handed to the client; coerced to at least one second.
    fallback_html : str
        Server-rendered HTML embedded in a ``<noscript>`` block for clients
        without JavaScript.

    Returns
    -------
    str
        Complete HTML page.
    """
    refresh = max(1, int(refresh_seconds))
    fleet_panel = (
        '<section class="panel fleet" id="fleet"><div class="panel__head"><h2>Fleet</h2></div>'
        '<div class="panel__body"><svg class="fleet__svg" id="fleet-svg"></svg>'
        '<div class="fleet__legend">'
        '<span><i style="background:#4dd6c1"></i>live</span>'
        '<span><i style="background:#f2b441"></i>waiter only</span>'
        '<span><i style="background:#ff6b6b"></i>missing waiter</span>'
        "</div></div></section>"
    )
    board_panel = (
        '<section class="panel span-2"><div class="panel__head"><h2>Board</h2>'
        '<span class="panel__count" id="board-count">0</span></div>'
        '<div class="lanes" id="lanes"></div></section>'
    )
    risk_panel = (
        '<section class="panel risk" id="risk-panel"><div class="panel__head"><h2>Risk</h2>'
        '<span class="risk__verdict risk__verdict--green" id="risk-verdict">—</span></div>'
        '<div class="panel__body" id="risk"></div></section>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SYNAPSE CHANNEL — fleet nerve center</title>
  <link rel="stylesheet" href="cockpit.css">
</head>
<body>
  <header class="hud">
    <div class="hud__mark"><b>SYNAPSE</b><span>CHANNEL</span></div>
    <div class="beacon beacon--stale" id="beacon">
      <span class="beacon__dot"></span><span id="beacon-label">connecting</span>
    </div>
    <div class="hud__vitals" id="vitals"></div>
  </header>
  <main class="deck">
    <div class="col">
      {fleet_panel}
      {board_panel}
      {_panel("Active claims", "claims", count_id="claims-count")}
      {_panel("Capability manifest", "manifest", count_id="manifest-count")}
    </div>
    <div class="col">
      {risk_panel}
      {_panel("Signal stream", "feed", scroll=True)}
      {_panel("Release receipts", "receipts", count_id="receipts-count", scroll=True)}
    </div>
  </main>
  <div class="veil" id="veil">
    <div class="veil__box">
      <h3>Dashboard token</h3>
      <p>This hub requires a bearer token to read snapshots. Paste it to continue.</p>
      <input id="veil-input" type="password" autocomplete="off" placeholder="bearer token">
      <button class="btn" id="veil-submit" type="button">Connect</button>
    </div>
  </div>
  <noscript><div class="noscript-fallback">{fallback_html}</div></noscript>
  <script>
    window.__SYN_COCKPIT__ = {{ refreshSeconds: {refresh}, snapshotUrl: "snapshot.json" }};
  </script>
  <script src="cockpit.js"></script>
</body>
</html>
"""
