# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — render the README header banner
"""Render the SYNAPSE CHANNEL README header banner.

Produces a 1280x640 PNG depicting the coordination bus: one central hub with
several agent nodes around it and message paths flowing between them. The image
is deterministic. Requires ``matplotlib`` (``pip install matplotlib``); it is a
build-time asset generator, not a runtime dependency.

Usage::

    python docs/assets/generate_header.py
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, RegularPolygon

WIDTH, HEIGHT = 12.8, 6.4
DPI = 100

BG = "#0d1117"
GRID = "#161b22"
HUB_EDGE = "#a78bfa"
HUB_FILL = "#8b5cf6"
PATH = "#30363d"
PULSE = "#fbbf24"
TEXT = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#a78bfa"

NODE_COLORS = ["#fbbf24", "#a78bfa", "#34d399", "#60a5fa", "#f472b6", "#22d3ee", "#fb923c"]


def _bezier(p0: np.ndarray, p2: np.ndarray, bend: float) -> np.ndarray:
    """Return points along a quadratic bezier from ``p0`` to ``p2``."""
    mid = (p0 + p2) / 2.0
    direction = p2 - p0
    normal = np.array([-direction[1], direction[0]])
    norm = np.linalg.norm(normal)
    control = mid + (normal / norm) * bend if norm else mid
    t = np.linspace(0.0, 1.0, 80)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * control + t**2 * p2


def generate_header(output_path: str = "header.png") -> None:
    """Render the banner and write it to ``output_path``."""
    rng = np.random.default_rng(42)
    fig = plt.figure(figsize=(WIDTH, HEIGHT), dpi=DPI, facecolor=BG)
    ax = fig.add_axes((0, 0, 1, 1), frameon=False)
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(0, HEIGHT)
    ax.axis("off")

    # Background grid.
    for x in np.arange(0, WIDTH, 0.5):
        ax.axvline(x, color=GRID, lw=0.5, alpha=0.6)
    for y in np.arange(0, HEIGHT, 0.5):
        ax.axhline(y, color=GRID, lw=0.5, alpha=0.6)

    hub = np.array([WIDTH * 0.66, HEIGHT * 0.5])
    n_agents = 7
    radius = 2.35
    agents = []
    for i in range(n_agents):
        angle = math.pi / 2 + i * (2 * math.pi / n_agents)
        jitter = rng.uniform(-0.12, 0.12, size=2)
        agents.append(hub + radius * np.array([math.cos(angle), math.sin(angle)]) + jitter)

    # Message paths from each agent to the hub, with travelling pulses.
    for i, agent in enumerate(agents):
        bend = 0.55 if i % 2 == 0 else -0.55
        curve = _bezier(agent, hub, bend)
        ax.plot(curve[:, 0], curve[:, 1], color=PATH, lw=1.6, alpha=0.9, zorder=1)
        for frac in (0.35, 0.7):
            idx = int(frac * (len(curve) - 1))
            ax.scatter(*curve[idx], s=26, color=PULSE, alpha=0.85, zorder=2, edgecolors="none")

    # Hub: a glowing hexagon with concentric halo.
    for r, alpha in ((1.5, 0.06), (1.2, 0.10), (0.95, 0.16)):
        ax.add_patch(RegularPolygon(tuple(hub), 6, radius=r, color=HUB_FILL, alpha=alpha, zorder=2))
    ax.add_patch(
        RegularPolygon(
            tuple(hub), 6, radius=0.95, facecolor=BG, edgecolor=HUB_EDGE, lw=2.6, zorder=3
        )
    )
    ax.text(
        hub[0],
        hub[1],
        "HUB",
        color=HUB_EDGE,
        fontsize=15,
        fontweight="bold",
        ha="center",
        va="center",
        zorder=4,
        family="monospace",
    )

    # Agent nodes.
    for i, agent in enumerate(agents):
        colour = NODE_COLORS[i % len(NODE_COLORS)]
        ax.add_patch(Circle(tuple(agent), 0.34, color=colour, alpha=0.16, zorder=3))
        ax.add_patch(Circle(tuple(agent), 0.24, facecolor=BG, edgecolor=colour, lw=2.0, zorder=4))

    # Title block (left).
    ax.text(
        0.55,
        HEIGHT * 0.70,
        "SYNAPSE",
        color=TEXT,
        fontsize=46,
        fontweight="bold",
        ha="left",
        va="center",
        family="sans-serif",
    )
    ax.text(
        0.55,
        HEIGHT * 0.52,
        "CHANNEL",
        color=ACCENT,
        fontsize=46,
        fontweight="bold",
        ha="left",
        va="center",
        family="sans-serif",
    )
    ax.text(
        0.6,
        HEIGHT * 0.34,
        "Local-first multi-agent coordination bus",
        color=MUTED,
        fontsize=15.5,
        ha="left",
        va="center",
        family="sans-serif",
    )
    ax.text(
        0.62,
        HEIGHT * 0.20,
        "one hub  ·  many agents  ·  no collisions",
        color=PULSE,
        fontsize=12.5,
        ha="left",
        va="center",
        family="monospace",
    )

    fig.savefig(output_path, dpi=DPI, facecolor=BG)
    plt.close(fig)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "header.png"
    generate_header(str(target))
    print(f"wrote {target}")
