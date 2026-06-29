# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio design-system reference page (A0)
"""The Studio design-system reference page.

This is the A0 foundation of the operator Studio: a single self-contained page that
exercises every component in `dashboard_assets/studio.css` — the verdict pills, status
dots, panels, cards, mono data rows, display numerals, and the navigation rail — in
the instrument-panel language the command centre will be built in. It is served at
``/studio`` and works with the hub offline (it renders no live data), so it doubles as
the visual reference Stage B assembles the real command centre from, and as a preview
of where the dashboard is heading.

It deliberately renders *static* sample content: A0 ships the look, Stage B wires the
live ``/studio.json`` projection into the same components.
"""

from __future__ import annotations

STUDIO_REFERENCE_PATH = "/studio"
"""HTTP path the Studio reference page is served at."""


def _verdict(tone: str, label: str) -> str:
    return f'<span class="syn-verdict syn-verdict--{tone}">{label}</span>'


def _dot_row(state: str, name: str, role: str, note: str) -> str:
    return (
        f'<div class="syn-row"><span class="syn-dot syn-dot--{state}"></span>'
        f"<span>{name}</span><span style='color:var(--syn-muted)'>{role}</span>"
        f"<span style='margin-left:auto;color:var(--syn-muted)'>{note}</span></div>"
    )


def render_studio_reference_html() -> str:
    """Render the Studio design-system reference page (no live data, hub-independent)."""
    # every status-dot state, so the reference shows the full vocabulary
    agents = "".join(
        (
            _dot_row("ok", "alpha", "ci", "claim core/"),
            _dot_row("warn", "beta", "docs", "lease 84%"),
            _dot_row("bad", "gamma", "ci", "conflict"),
            _dot_row("dark", "delta", "—", "dark"),
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SYNAPSE Studio — design system</title>
  <link rel="stylesheet" href="studio.css">
</head>
<body class="syn" style="margin:0;padding:var(--syn-sp-5)">
  <nav class="syn-nav" style="margin-bottom:var(--syn-sp-5)">
    <a href="{STUDIO_REFERENCE_PATH}" aria-current="page">command</a>
    <a href="#">workflow</a><a href="#">trace</a><a href="#">policy</a>
    <a href="#">routing</a><a href="#">channels</a>
  </nav>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--syn-sp-4)">
    <section class="syn-panel">
      <div class="syn-label">verdict</div>
      <div style="margin:var(--syn-sp-3) 0">{_verdict("amber", "Amber")}</div>
      <div class="syn-num">2</div>
      <div class="syn-label">blocked</div>
    </section>
    <section class="syn-panel">
      <div class="syn-label">agents</div>
      <div style="margin-top:var(--syn-sp-3)">{agents}</div>
    </section>
    <section class="syn-panel">
      <div class="syn-label">verdict scale</div>
      <div class="syn-stack" style="margin-top:var(--syn-sp-3)">
        {_verdict("green", "Green")}{_verdict("amber", "Amber")}{_verdict("red", "Red")}
      </div>
      <div class="syn-card" style="margin-top:var(--syn-sp-4)">
        <div class="syn-label">card</div>
        <a href="{STUDIO_REFERENCE_PATH}">a focusable link</a>
      </div>
    </section>
  </div>
</body>
</html>
"""
