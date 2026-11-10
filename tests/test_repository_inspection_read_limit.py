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
- a configuration of exactly the limit is read whole and reported configured, so the
  same gate is ready wherever file modes carry execute bits;
- the read counts and returns the file's raw bytes: CRLF line ends and a Ctrl-Z byte,
  which a Windows text-mode descriptor would translate or stop at, change nothing.

The growth case runs in a child interpreter bound to the candidate package under
test, and the child reports the origin of the module it inspected with.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cli_e2e_helpers import _candidate_environment, git_repo
from synapse_channel import mutation_governance
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

# The staged gate trusts a hook only when the mode from its open descriptor has an
# execute bit. Native Windows reports a regular file as 0o444 or 0o666 whatever chmod
# asked for, so there the same real hook reads as invalid and the gate cannot be
# ready. The configuration read, and so the configured/invalid outcome, is the same
# on every platform.
_POSIX_MODES = os.name != "nt"
_HOOK_STATE = "installed" if _POSIX_MODES else "invalid"
_CONFIGURED_GATE = (
    ("ready-not-exercised", "not-exercised") if _POSIX_MODES else ("incomplete", "not-configured")
)


def _gated_repository(tmp_path: Path, config_bytes: bytes) -> tuple[Path, Path, Path]:
    """Return a home, a real Git repository with an installed hook, and its configuration."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    repo = git_repo(tmp_path / "repo")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    config = repo / ".pre-commit-config.yaml"
    config.write_bytes(config_bytes)
    return home, repo, config


_GROWTH_PROGRAM = r"""
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from synapse_channel import mutation_governance
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
origin = str(Path(mutation_governance.__file__).resolve())
sys.stdout.write(json.dumps({"origin": origin, "gate": asdict(report.staged_gate)}))
"""


def test_a_configuration_that_grows_during_its_read_leaves_the_gate_incomplete(
    tmp_path: Path,
) -> None:
    """Growth after the open-time checks is caught by the read loop, not read as configured.

    The configuration has CRLF line ends, so a read that translated them would count
    fewer bytes than the file holds and miss the limit.
    """
    crlf_gate = _GATE.replace("\n", "\r\n").encode("utf-8")
    home, repo, config = _gated_repository(tmp_path, crlf_gate)
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
        env=_candidate_environment(None),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    answer = json.loads(result.stdout)
    assert answer["origin"] == str(Path(mutation_governance.__file__).resolve())
    gate = answer["gate"]
    assert gate["repository"] is not None
    assert (gate["configuration_state"], gate["hook_state"]) == ("invalid", _HOOK_STATE)
    assert (gate["gate_status"], gate["enforcement_status"]) == ("incomplete", "not-configured")
    after = config.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert after.st_size == _READ_LIMIT + 1
    assert config.read_bytes().startswith(crlf_gate)


def test_a_configuration_of_exactly_the_limit_is_read_as_configured(
    tmp_path: Path,
) -> None:
    """The byte limit is inclusive: a configured gate of exactly the limit is accepted."""
    padding = "#" * (_READ_LIMIT - len(_GATE.encode("utf-8")) - 1) + "\n"
    home, repo, config = _gated_repository(tmp_path, (_GATE + padding).encode("utf-8"))
    assert config.stat().st_size == _READ_LIMIT
    before = config.read_bytes()

    gate = inspect_mutation_governance(home=home, project=repo).staged_gate

    assert (gate.configuration_state, gate.hook_state) == ("configured", _HOOK_STATE)
    assert (gate.gate_status, gate.enforcement_status) == _CONFIGURED_GATE
    assert os.path.samefile(gate.repository or "", repo)
    assert config.read_bytes() == before


def test_a_ctrl_z_byte_does_not_end_the_configuration_read(tmp_path: Path) -> None:
    """A Ctrl-Z byte before the gate does not hide the gate from the inspection."""
    home, repo, config = _gated_repository(tmp_path, b"# \x1a\n" + _GATE.encode("utf-8"))

    gate = inspect_mutation_governance(home=home, project=repo).staged_gate

    assert (gate.configuration_state, gate.hook_state) == ("configured", _HOOK_STATE)
    assert (gate.gate_status, gate.enforcement_status) == _CONFIGURED_GATE
