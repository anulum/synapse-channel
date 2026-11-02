# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — synapse mcp over HTTPS names the missing extra before touching any file
"""A valid private HTTPS profile without the MCP server stack exits 1 and names the extra."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from synapse_channel import cli


@pytest.mark.parametrize("module", ["uvicorn", "pydantic"])
def test_a_missing_server_dependency_names_the_mcp_extra(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: str,
) -> None:
    """The listener is never prepared: no key, grant or certificate path is read."""
    monkeypatch.setitem(sys.modules, module, None)
    code = cli.main(
        [
            "mcp",
            "--transport",
            "streamable-http",
            "--project",
            "ALPHA",
            "--uri",
            "ws://127.0.0.1:1",
            "--http-auth-file",
            str(tmp_path / "grants.json"),
            "--tls-cert-file",
            str(tmp_path / "https.pem"),
            "--tls-key-file",
            str(tmp_path / "https-key.pem"),
            "--http-port",
            "8443",
            "--http-allowed-host",
            "127.0.0.1:8443",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "synapse mcp: install synapse-channel[mcp] for HTTPS\n"
    assert captured.out == ""
    assert list(tmp_path.iterdir()) == []
