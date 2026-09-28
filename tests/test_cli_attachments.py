# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — attachment CLI activation refuses an ungoverned hub
"""The public Hub parser exposes C12 only behind its complete posture."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser


def test_attachment_root_parser_and_fail_closed_startup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The opt-in path is parsed but never created under the default open posture."""
    root = tmp_path / "attachments"
    args = build_parser().parse_args(["hub", "--attachment-root", str(root)])
    assert args.attachment_root == str(root)
    ns = _hub_ns(attachment_root=str(root))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert not root.exists()
    assert "--attachment-root requires" in capsys.readouterr().err
