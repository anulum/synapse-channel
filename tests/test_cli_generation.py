# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI and isolated-subprocess tests for `synapse generation`
"""Verify ``synapse generation head|read|publish|seal`` in-process and across subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_generation import EXIT_INVALID, EXIT_OK, EXIT_REFUSED
from synapse_channel.core.generation_chain import EMPTY_CHAIN_ARTEFACT
from synapse_channel.core.secure_path import apply_owner_only_dir

SHA_A = "1" * 64
SHA_B = "2" * 64


def _chain_dir(tmp_path: Path, name: str = "chain") -> Path:
    chain = tmp_path / name
    chain.mkdir()
    apply_owner_only_dir(chain)
    return chain


def test_cli_generation_in_process_exit_codes_and_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise head, read, publish, seal, and invalid-input refusals through ``cli.main``."""
    chain = _chain_dir(tmp_path)

    assert main(["generation", "head", "--chain", str(chain)]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"head": 0}

    assert main(["generation", "read", "--chain", str(chain), "--generation", "1"]) == EXIT_REFUSED
    assert json.loads(capsys.readouterr().out) == {"generation": 1, "status": "absent"}

    # Publish generation 1 -> exit 0 (`created`), second publish at base 0 -> exit 1 (`refused`).
    assert (
        main(
            [
                "generation",
                "publish",
                "--chain",
                str(chain),
                "--base",
                "0",
                "--operation",
                "op-1",
                "--attempt",
                "att-1",
                "--artefact",
                SHA_A,
            ]
        )
        == EXIT_OK
    )
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "created"
    assert created["record"]["generation"] == 1
    assert created["record"]["artefact"] == SHA_A

    assert (
        main(
            [
                "generation",
                "publish",
                "--chain",
                str(chain),
                "--base",
                "0",
                "--operation",
                "op-1",
                "--attempt",
                "att-2",
                "--artefact",
                SHA_B,
            ]
        )
        == EXIT_REFUSED
    )
    refused = json.loads(capsys.readouterr().out)
    assert refused["status"] == "refused"
    assert refused["record"]["attempt"] == "att-1"

    # Refused seal at base 0 reads back the winning `applied` record.
    assert (
        main(
            [
                "generation",
                "seal",
                "--chain",
                str(chain),
                "--base",
                "0",
                "--operation",
                "op-1",
                "--attempt",
                "att-1",
            ]
        )
        == EXIT_REFUSED
    )
    refused_seal = json.loads(capsys.readouterr().out)
    assert refused_seal["status"] == "refused"
    assert refused_seal["record"]["kind"] == "applied"

    # Winning seal at base 1 carries SHA_A forward -> exit 0 (`created`).
    assert (
        main(
            [
                "generation",
                "seal",
                "--chain",
                str(chain),
                "--base",
                "1",
                "--operation",
                "op-2",
                "--attempt",
                "att-3",
            ]
        )
        == EXIT_OK
    )
    sealed = json.loads(capsys.readouterr().out)
    assert sealed["status"] == "created"
    assert sealed["record"]["kind"] == "sealed"
    assert sealed["record"]["artefact"] == SHA_A

    assert main(["generation", "read", "--chain", str(chain), "--generation", "2"]) == EXIT_OK
    read_two = json.loads(capsys.readouterr().out)
    assert read_two["kind"] == "sealed"
    assert read_two["artefact"] == SHA_A

    # Invalid integer formats and generation 0 on `read` exit 2 with stderr message.
    assert main(["generation", "read", "--chain", str(chain), "--generation", "0"]) == EXIT_INVALID
    assert "at least 1" in capsys.readouterr().err
    assert (
        main(
            [
                "generation",
                "seal",
                "--chain",
                str(chain),
                "--base",
                "01",
                "--operation",
                "op-2",
                "--attempt",
                "att-4",
            ]
        )
        == EXIT_INVALID
    )
    assert "canonical non-negative integer" in capsys.readouterr().err


def test_cli_generation_runs_from_external_process_without_importing_package(
    tmp_path: Path,
) -> None:
    """Drive ``synapse generation`` from a clean Python process without importing Core."""
    chain = _chain_dir(tmp_path, "external-chain")
    repo_src = str(Path(__file__).resolve().parents[1] / "src")

    driver = tmp_path / "external_adapter.py"
    driver.write_text(
        """from __future__ import annotations
import json
import os
import subprocess
import sys

assert not any(name.startswith("synapse_channel") for name in sys.modules)
chain = sys.argv[1]
child_env = dict(os.environ, PYTHONPATH=sys.argv[2])

def call(*args: str) -> tuple[int, dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "generation", *args],
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, json.loads(proc.stdout)

rc_h0, h0 = call("head", "--chain", chain)
assert rc_h0 == 0 and h0 == {"head": 0}

rc_s0, s0 = call("seal", "--chain", chain, "--base", "0", "--operation", "op-0", "--attempt", "a0")
assert rc_s0 == 0 and s0["status"] == "created"
assert s0["record"]["artefact"] == "0" * 64

rc_p0, p0 = call(
    "publish", "--chain", chain, "--base", "0",
    "--operation", "op-0", "--attempt", "a0", "--artefact", "f" * 64,
)
assert rc_p0 == 1 and p0["status"] == "refused"
assert p0["record"]["kind"] == "sealed"

rc_p1, p1 = call(
    "publish", "--chain", chain, "--base", "1",
    "--operation", "op-1", "--attempt", "a1", "--artefact", "f" * 64,
)
assert rc_p1 == 0 and p1["status"] == "created"

rc_r2, r2 = call("read", "--chain", chain, "--generation", "2")
assert rc_r2 == 0 and r2["kind"] == "applied" and r2["artefact"] == "f" * 64
assert not any(name.startswith("synapse_channel") for name in sys.modules)
print("EXTERNAL_ADAPTER_OK")
""",
        encoding="utf-8",
    )

    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, "-I", str(driver), str(chain), repo_src],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EXTERNAL_ADAPTER_OK"
    assert (chain / "gen-00000000000000000001.json").is_file()
    assert EMPTY_CHAIN_ARTEFACT in (chain / "gen-00000000000000000001.json").read_text(
        encoding="ascii"
    )
