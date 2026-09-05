# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — host monitor rendered Python reference
"""Exercise the normal documentation renderer for the public monitor surface."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.docs_contract
def test_host_monitor_reference_renders_source_docstrings(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    site = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rendered = (site / "api" / "index.html").read_text(encoding="utf-8")
    for module, symbols in {
        "host_sessions": ("HostSession", "HostObservation", "HostSessionMonitor"),
        "host_sessions_proc": (
            "ProcessIdentity",
            "ProcessMetadata",
            "KernelClock",
            "observe_process",
            "discover_processes",
            "process_metadata",
            "kernel_clock",
        ),
        "host_sessions_tmux": ("PaneMetadata", "observe_tmux"),
        "dashboard_host_sessions": ("load_host_grants", "host_session_response"),
        "cli_pid_monitor": ("render_host_observation", "format_runtime"),
    }.items():
        for symbol in symbols:
            assert f'id="synapse_channel.{module}.{symbol}"' in rendered
    assert 'id="synapse_channel.host_sessions.HostSessionMonitor.snapshot"' in rendered
    assert 'id="synapse_channel.host_sessions.HostObservation.to_json"' in rendered
    assert "Return an observation cached for one second per disclosure profile." in rendered
    assert "Authorise before observation and recheck grants before serialisation." in rendered
