# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the byte limit of the mutation-posture repository file read
"""Pin the inclusive byte limit of the pre-commit configuration read by mutation posture.

The open-time checks refuse a configuration that is already over the limit or was
swapped. These tests cover the read loop behind them, through the public
``inspect_mutation_governance`` on a real Git repository with a real configuration and
pre-commit hook:

- a configuration that grows after those checks is refused by the loop's own byte
  count, so the gate is reported incomplete;
- a configuration of exactly the limit is read whole, so the same gate is ready.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cli_e2e_helpers import git_repo
from synapse_channel.mutation_governance import _READ_LIMIT, inspect_mutation_governance

_GATE = """\
repos:
- repo: local
  hooks:
  - id: staged-claim-coverage
    stages: [pre-commit]
    always_run: true
    pass_filenames: false
    entry: run_staged_claim_hook.py
"""

_HOOK = "#!/bin/sh\nexec python3 -m pre_commit hook-impl --hook-type=pre-commit\n"


def _gated_repository(tmp_path: Path, config_text: str) -> tuple[Path, Path, Path]:
    """Return a home, a real Git repository with an installed hook, and its configuration."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = git_repo(tmp_path / "repo")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    config = repo / ".pre-commit-config.yaml"
    config.write_text(config_text, encoding="utf-8")
    return home, repo, config


_GROWTH_PROGRAM = r"""
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from synapse_channel.mutation_governance import inspect_mutation_governance

home, repo, config, limit_text = sys.argv[1:5]
limit = int(limit_text)
grown = False

def grow_at_the_first_configuration_read(frame, event, arg):
    global grown
    if grown or event != "c_call" or arg is not os.read:
        return
    if frame.f_code.co_name != "_read_repository_text":
        return
    if Path(frame.f_locals["path"]).name != ".pre-commit-config.yaml":
        return
    grown = True
    extra = (limit + 1) - Path(config).stat().st_size
    descriptor = os.open(config, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, b"#" * extra)
    finally:
        os.close(descriptor)

sys.setprofile(grow_at_the_first_configuration_read)
report = inspect_mutation_governance(home=Path(home), project=Path(repo))
sys.setprofile(None)
if not grown:
    raise SystemExit("the configuration never grew during its read")
sys.stdout.write(json.dumps(asdict(report.staged_gate)))
"""


def test_a_configuration_that_grows_during_its_read_leaves_the_gate_incomplete(
    tmp_path: Path,
) -> None:
    """Growth after the open-time checks is caught by the read loop, not read as configured."""
    home, repo, config = _gated_repository(tmp_path, _GATE)
    before = config.stat()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _GROWTH_PROGRAM,
            str(home),
            str(repo),
            str(config),
            str(_READ_LIMIT),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    gate = json.loads(result.stdout)
    assert gate["repository"] is not None
    assert (gate["configuration_state"], gate["hook_state"]) == ("invalid", "installed")
    assert (gate["gate_status"], gate["enforcement_status"]) == ("incomplete", "not-configured")
    after = config.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert after.st_size == _READ_LIMIT + 1
    assert config.read_text(encoding="utf-8").startswith(_GATE)


def test_a_configuration_of_exactly_the_limit_is_read_and_the_gate_is_ready(
    tmp_path: Path,
) -> None:
    """The byte limit is inclusive: a configured gate of exactly the limit is accepted."""
    padding = "#" * (_READ_LIMIT - len(_GATE.encode("utf-8")) - 1) + "\n"
    home, repo, config = _gated_repository(tmp_path, _GATE + padding)
    assert config.stat().st_size == _READ_LIMIT
    before = config.read_bytes()

    gate = inspect_mutation_governance(home=home, project=repo).staged_gate

    assert (gate.configuration_state, gate.hook_state) == ("configured", "installed")
    assert (gate.gate_status, gate.enforcement_status) == ("ready-not-exercised", "not-exercised")
    assert os.path.samefile(gate.repository or "", repo)
    assert config.read_bytes() == before
