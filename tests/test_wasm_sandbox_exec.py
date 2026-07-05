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
from synapse_channel.core.wasm_sandbox import preflight_sandboxed, run_sandboxed

wasmtime = pytest.importorskip("wasmtime")

_DIGEST = "sha256:" + "a" * 64
_RUN_42 = wasmtime.wat2wasm('(module (func (export "run") (result i32) i32.const 42))')
_SPIN = wasmtime.wat2wasm('(module (func (export "run") (result i32) (loop $l br $l) i32.const 0))')
_UNREACHABLE = wasmtime.wat2wasm('(module (func (export "run") (result i32) unreachable))')
_NO_RUN = wasmtime.wat2wasm('(module (func (export "other") (result i32) i32.const 1))')


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


def test_run_records_the_resolved_preopened_paths(tmp_path: Path) -> None:
    # A filesystem grant is preopened as its canonical real directory, and the receipt records
    # exactly where the sandbox reached — resolved, not the manifest's literal string.
    work = tmp_path / "work"
    work.mkdir()
    host = str(work.resolve())  # already canonical, so it survives host-path hardening
    manifest = CapabilityManifest(
        tool_id="calc",
        content_digest=_DIGEST,
        resources=ResourceGrant(memory_bytes=1 << 20, fuel=1_000_000, wall_clock_ms=2_000),
        filesystem=(FilesystemGrant(host_path=host, guest_path="/data", write=False),),
    )
    receipt = run_sandboxed(manifest, _RUN_42, b"")
    assert receipt["exit"] == EXIT_OK
    assert receipt["preopened_paths"] == [host]


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


def _matching_manifest(wasm_bytes: bytes) -> CapabilityManifest:
    """A manifest whose digest matches ``wasm_bytes``, so only the entrypoint check varies."""
    return CapabilityManifest(
        tool_id="calc",
        content_digest=digest_bytes(wasm_bytes),
        resources=ResourceGrant(memory_bytes=1 << 20, fuel=1_000_000, wall_clock_ms=2_000),
    )


def test_preflight_passes_a_runnable_tool() -> None:
    report = preflight_sandboxed(_matching_manifest(_RUN_42), _RUN_42)
    assert report["ok"] is True
    assert report["module_valid"] is True
    assert report["entrypoint_exported"] is True
    assert report["digest_matches"] is True
    assert "run" in report["exported_functions"]
    assert report["reason"] == ""


def test_preflight_does_not_execute_the_tool() -> None:
    # _SPIN loops forever if run; a preflight only compiles and inspects exports, so it
    # returns immediately and reports the tool ready — proof that nothing was executed.
    report = preflight_sandboxed(_matching_manifest(_SPIN), _SPIN)
    assert report["ok"] is True
    assert report["entrypoint_exported"] is True


def test_preflight_reports_a_missing_entrypoint() -> None:
    report = preflight_sandboxed(_matching_manifest(_NO_RUN), _NO_RUN)
    assert report["ok"] is False
    assert report["module_valid"] is True
    assert report["entrypoint_exported"] is False
    assert report["exported_functions"] == ["other"]
    assert report["reason"] == "entrypoint 'run' is not an exported function"


def test_preflight_honours_a_custom_entrypoint() -> None:
    report = preflight_sandboxed(_matching_manifest(_NO_RUN), _NO_RUN, entrypoint="other")
    assert report["ok"] is True
    assert report["entrypoint_exported"] is True


def test_preflight_reports_a_malformed_module() -> None:
    bad = b"this is not a wasm module"
    report = preflight_sandboxed(_matching_manifest(bad), bad)
    assert report["ok"] is False
    assert report["module_valid"] is False
    assert report["exported_functions"] == []
    assert report["reason"].startswith("module is not valid WebAssembly:")


def test_preflight_flags_a_module_that_does_not_match_its_manifest() -> None:
    # a valid, runnable module, but the manifest was written for a different one
    mismatched = CapabilityManifest(tool_id="calc", content_digest=_DIGEST)
    report = preflight_sandboxed(mismatched, _RUN_42)
    assert report["ok"] is False
    assert report["module_valid"] is True
    assert report["entrypoint_exported"] is True
    assert report["digest_matches"] is False
    assert "does not match the manifest" in report["reason"]


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
