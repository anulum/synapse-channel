# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — golden-demo evidence artifact writer
"""Write machine-readable evidence and a static dashboard for the golden demo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.dashboard import DashboardSnapshot
from synapse_channel.dashboard_render import render_dashboard_html


class GoldenDemoEvidence(Protocol):
    """Minimal golden-demo result surface required by the artifact writer.

    Attributes
    ----------
    dashboard : DashboardSnapshot
        Live snapshot rendered into the static operator dashboard.
    """

    @property
    def dashboard(self) -> DashboardSnapshot:
        """Return the live snapshot rendered into the static dashboard."""

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-serialisable evidence document."""


@dataclass(frozen=True)
class DemoArtifacts:
    """Paths of the evidence documents emitted by ``synapse demo``.

    Attributes
    ----------
    evidence_json : pathlib.Path
        Machine-readable completion, guard, receipt, and snapshot evidence.
    dashboard_html : pathlib.Path
        Static rendered dashboard showing the seven-step story.
    """

    evidence_json: Path
    dashboard_html: Path


def _atomic_write(path: Path, payload: str) -> None:
    """Atomically replace ``path`` with UTF-8 ``payload``."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def write_demo_artifacts(result: GoldenDemoEvidence, output_dir: Path) -> DemoArtifacts:
    """Write stable JSON evidence and the dashboard story to ``output_dir``.

    Parameters
    ----------
    result : GoldenDemoEvidence
        Completed demo result carrying structured evidence and the live snapshot.
    output_dir : pathlib.Path
        Destination directory, created when absent.

    Returns
    -------
    DemoArtifacts
        Absolute paths of the written JSON and HTML documents.
    """
    target = output_dir.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    evidence_path = target / "golden-demo.json"
    dashboard_path = target / "golden-demo-dashboard.html"
    evidence = json.dumps(
        result.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    dashboard = render_dashboard_html(result.dashboard, refresh_seconds=3600, static=True)
    _atomic_write(evidence_path, evidence + "\n")
    _atomic_write(dashboard_path, dashboard)
    return DemoArtifacts(
        evidence_json=evidence_path,
        dashboard_html=dashboard_path,
    )
