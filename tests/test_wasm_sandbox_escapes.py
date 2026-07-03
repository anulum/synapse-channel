# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — adversarial proofs that the WASM sandbox limits hold

"""Escape-attempt battery: each test is a hostile tool the sandbox must contain.

The happy-path execution tests live in ``test_wasm_sandbox_exec``; these are the
adversary's tests — a memory bomb, a fuel bomb, a wall-clock runaway, a module
reaching for a host syscall, and a module reaching for the network. Every one
must be stopped by a *mechanism*, not by the tool's good behaviour, and the
receipt must record the containment so an auditor sees it happened. Where a
guarantee is structural (an undefined import cannot link), the test asserts the
structure; where it is a runtime limit (fuel, epoch, memory), the test drives
the tool past the limit and reads the receipt.
"""

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
from synapse_channel.core.wasm_sandbox import derive_runtime_config, run_sandboxed

wasmtime = pytest.importorskip("wasmtime")

_DIGEST = "sha256:" + "a" * 64


def _manifest(
    *,
    memory_bytes: int = 1 << 16,
    fuel: int = 1_000_000,
    wall_clock_ms: int = 2_000,
    filesystem: tuple[FilesystemGrant, ...] = (),
) -> CapabilityManifest:
    return CapabilityManifest(
        tool_id="adversary",
        content_digest=_DIGEST,
        filesystem=filesystem,
        resources=ResourceGrant(memory_bytes=memory_bytes, fuel=fuel, wall_clock_ms=wall_clock_ms),
    )


# -- memory ---------------------------------------------------------------------

# One page start, then grow 2000 pages (~128 MiB) against a 64 KiB cap.
_MEMORY_BOMB = wasmtime.wat2wasm(
    '(module (memory 1) (func (export "run") (result i32) (memory.grow (i32.const 2000))))'
)


def test_a_memory_bomb_is_refused_the_pages() -> None:
    """A grow past the cap returns -1 — the allocation never happens."""
    receipt = run_sandboxed(_manifest(memory_bytes=1 << 16), _MEMORY_BOMB, b"")

    # memory.grow reports -1 on refusal; the guest keeps running but never got
    # the pages, so the host memory ceiling held.
    assert receipt["exit"] == EXIT_OK
    assert receipt["output_digest"] == digest_bytes(b"-1")


# -- fuel -----------------------------------------------------------------------

_FUEL_BOMB = wasmtime.wat2wasm(
    '(module (func (export "run") (result i32) (loop $l br $l) i32.const 0))'
)


def test_a_fuel_bomb_exhausts_and_is_receipted() -> None:
    receipt = run_sandboxed(_manifest(fuel=50_000), _FUEL_BOMB, b"")

    assert receipt["exit"] == EXIT_OUT_OF_FUEL
    assert receipt["fuel_used"] > 0


# -- wall clock -----------------------------------------------------------------

# A tight loop that consumes no fuel (fuel high) but never returns: only the
# epoch timer can stop it.
_SPIN = wasmtime.wat2wasm('(module (func (export "run") (result i32) (loop $l br $l) i32.const 0))')


def test_a_wall_clock_runaway_is_interrupted() -> None:
    receipt = run_sandboxed(_manifest(fuel=1_000_000_000, wall_clock_ms=50), _SPIN, b"")

    assert receipt["exit"] in {EXIT_EPOCH_DEADLINE, EXIT_OUT_OF_FUEL}


# -- host syscalls --------------------------------------------------------------

_HOST_SYSCALL = wasmtime.wat2wasm(
    '(module (import "env" "system" (func (param i32) (result i32)))'
    ' (func (export "run") (result i32) i32.const 0))'
)


def test_a_reach_for_a_host_syscall_does_not_link() -> None:
    """An arbitrary host import is undefined — the module cannot instantiate."""
    receipt = run_sandboxed(_manifest(), _HOST_SYSCALL, b"")

    assert receipt["exit"] == EXIT_ERROR
    assert "unknown import" in receipt["reason"]
    assert "env::system" in receipt["reason"]


# -- network --------------------------------------------------------------------


@pytest.mark.parametrize("opener", ["sock_connect", "sock_open"])
def test_no_import_opens_an_outbound_connection(opener: str) -> None:
    """The WASI functions that would open a socket are simply not defined.

    ``sock_recv``/``sock_send`` exist as stubs but act only on an already-open
    socket fd; with no ``sock_connect``/``sock_open`` to create one and no
    network granted to the WASI config, a tool has no path to the network at
    all. The denial is structural — an undefined import cannot link — not a
    setting that could be toggled on by accident.
    """
    module = wasmtime.wat2wasm(
        f'(module (import "wasi_snapshot_preview1" "{opener}" (func (param i32) (result i32)))'
        ' (func (export "run") (result i32) i32.const 0))'
    )

    receipt = run_sandboxed(_manifest(), module, b"")

    assert receipt["exit"] == EXIT_ERROR
    assert "unknown import" in receipt["reason"]
    assert opener in receipt["reason"]


# -- filesystem -----------------------------------------------------------------


def test_a_tool_with_no_grant_gets_no_preopened_directory() -> None:
    """No filesystem grant means no preopen — the tool starts with no fd to any path."""
    config = derive_runtime_config(_manifest())
    assert config.preopens == ()


def test_only_the_granted_paths_are_preopened(tmp_path: Path) -> None:
    """Exactly the manifest's grants become preopens — nothing above them."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()

    config = derive_runtime_config(
        _manifest(filesystem=(FilesystemGrant(str(allowed), "/work", write=False),))
    )

    hosts = {host for host, _guest, _write in config.preopens}
    assert hosts == {str(allowed)}
    assert str(forbidden) not in hosts
    assert str(tmp_path) not in hosts  # the parent is never implicitly reachable


def test_a_read_only_grant_never_derives_a_writable_preopen(tmp_path: Path) -> None:
    """A read-only grant maps to a read-only preopen — write is opt-in per grant."""
    ro = tmp_path / "ro"
    ro.mkdir()

    config = derive_runtime_config(
        _manifest(filesystem=(FilesystemGrant(str(ro), "/ro", write=False),))
    )

    assert config.preopens == ((str(ro), "/ro", False),)
