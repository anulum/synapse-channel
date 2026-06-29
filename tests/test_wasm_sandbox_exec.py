# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real wasmtime execution: limits are enforced, runaways are stopped

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.sandbox_policy import (
    CapabilityManifest,
    FilesystemGrant,
    ResourceGrant,
)
from synapse_channel.core.sandbox_receipt import (
    EXIT_EPOCH_DEADLINE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_OUT_OF_FUEL,
    digest_bytes,
)
from synapse_channel.core.wasm_sandbox import run_sandboxed

wasmtime = pytest.importorskip("wasmtime")

_DIGEST = "sha256:" + "a" * 64
_RUN_42 = wasmtime.wat2wasm('(module (func (export "run") (result i32) i32.const 42))')
_SPIN = wasmtime.wat2wasm('(module (func (export "run") (result i32) (loop $l br $l) i32.const 0))')
_UNREACHABLE = wasmtime.wat2wasm('(module (func (export "run") (result i32) unreachable))')


def _manifest(*, fuel: int = 1_000_000, wall_clock_ms: int = 2_000) -> CapabilityManifest:
    return CapabilityManifest(
        tool_id="calc",
        content_digest=_DIGEST,
        resources=ResourceGrant(memory_bytes=1 << 20, fuel=fuel, wall_clock_ms=wall_clock_ms),
    )


def test_a_well_behaved_tool_runs_and_is_receipted() -> None:
    receipt = run_sandboxed(_manifest(), _RUN_42, b"input-data")
    assert receipt["exit"] == EXIT_OK
    assert receipt["fuel_used"] > 0
    assert receipt["output_digest"] == digest_bytes(b"42")
    assert receipt["inputs_digest"] == digest_bytes(b"input-data")
    assert receipt["reason"] == ""
    assert receipt["content_digest"] == _DIGEST


def test_a_fuel_bomb_is_stopped() -> None:
    receipt = run_sandboxed(_manifest(fuel=5_000), _SPIN, b"")
    assert receipt["exit"] == EXIT_OUT_OF_FUEL
    assert receipt["fuel_used"] == 5_000  # the whole budget was consumed before the trap


def test_a_wall_clock_runaway_is_interrupted() -> None:
    # ample fuel, tiny wall-clock: the epoch backstop must stop the infinite loop
    receipt = run_sandboxed(_manifest(fuel=10**12, wall_clock_ms=50), _SPIN, b"")
    assert receipt["exit"] == EXIT_EPOCH_DEADLINE


def test_an_unexpected_trap_is_an_error() -> None:
    receipt = run_sandboxed(_manifest(), _UNREACHABLE, b"")
    assert receipt["exit"] == EXIT_ERROR
    assert receipt["reason"]  # the trap message is recorded


def test_a_missing_entrypoint_is_an_error() -> None:
    receipt = run_sandboxed(_manifest(), _RUN_42, b"", entrypoint="absent")
    assert receipt["exit"] == EXIT_ERROR
    assert "absent" in receipt["reason"]


def test_a_non_module_is_an_error() -> None:
    receipt = run_sandboxed(_manifest(), b"this is not a wasm module", b"")
    assert receipt["exit"] == EXIT_ERROR


def test_filesystem_grants_are_preopened(tmp_path: Path) -> None:
    read_only = tmp_path / "ro"
    read_only.mkdir()
    writable = tmp_path / "rw"
    writable.mkdir()
    manifest = CapabilityManifest(
        tool_id="calc",
        content_digest=_DIGEST,
        filesystem=(
            FilesystemGrant(str(read_only), "/ro", write=False),
            FilesystemGrant(str(writable), "/rw", write=True),
        ),
        resources=ResourceGrant(memory_bytes=1 << 20, fuel=1_000_000, wall_clock_ms=2_000),
    )
    receipt = run_sandboxed(manifest, _RUN_42, b"")
    assert receipt["exit"] == EXIT_OK
    assert "fs:/ro:read" in receipt["granted_capabilities"]
    assert "fs:/rw:read_write" in receipt["granted_capabilities"]
