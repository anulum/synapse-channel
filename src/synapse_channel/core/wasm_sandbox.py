# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — capability-limited WebAssembly runtime for sandboxed tools
"""Run an untrusted ``.wasm`` tool with the capabilities a manifest grants — and only those.

This is the enforcement half of the sandbox. It takes a granted
:class:`~synapse_channel.core.sandbox_policy.CapabilityManifest`, derives a WebAssembly
runtime configuration from its grants, executes the tool's entrypoint under those limits,
and returns a :class:`~synapse_channel.core.sandbox_receipt.RunReceipt`. The tool gets no
ambient authority: memory and fuel are capped, a wall-clock backstop interrupts a runaway,
the filesystem is limited to the manifest's preopened paths, and the network is denied by
construction — WASI preview1 exposes no sockets, so a tool reaches the network only through
a host import that is never linked.

The runtime (``wasmtime``) is a heavy dependency and lives behind the optional ``[wasm]``
extra; it is imported only inside :func:`_require_wasm`, so importing this module never
pulls it in. The manifest→config derivation is pure and testable without the extra; only
:func:`run_sandboxed` needs the runtime. The :class:`WasmRuntime` protocol documents the
exact runtime surface used so the core depends on a shape, not a package.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from synapse_channel.core.sandbox_paths import SandboxPathError, harden_preopens
from synapse_channel.core.sandbox_policy import CapabilityManifest
from synapse_channel.core.sandbox_receipt import (
    EXIT_EPOCH_DEADLINE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_OUT_OF_FUEL,
    PreflightReport,
    RunReceipt,
    build_preflight_report,
    build_run_receipt,
    digest_bytes,
)

WASM_EXTRA_HINT = "the WASM sandbox needs the optional extra: pip install 'synapse-channel[wasm]'"
"""Install hint raised when the optional ``wasmtime`` runtime is absent."""

DEFAULT_ENTRYPOINT = "run"
"""The exported function a sandboxed tool is called through by default."""


class WasmRuntime(Protocol):
    """The ``wasmtime`` surface this module uses; the core depends on this shape only."""

    Config: Any
    Engine: Any
    Store: Any
    Module: Any
    Linker: Any
    WasiConfig: Any
    DirPerms: Any
    FilePerms: Any
    FuncType: Any
    Trap: Any
    WasmtimeError: Any
    TrapCode: Any


@dataclass(frozen=True)
class SandboxRuntimeConfig:
    """The runtime limits derived from a manifest: memory, fuel, wall-clock, and preopens."""

    memory_bytes: int
    fuel: int
    wall_clock_ms: int
    preopens: tuple[tuple[str, str, bool], ...]


def derive_runtime_config(manifest: CapabilityManifest) -> SandboxRuntimeConfig:
    """Derive the WebAssembly runtime limits from a granted manifest.

    Pure: maps the resource budget to memory/fuel/wall-clock and each filesystem grant to a
    ``(host, guest, write)`` preopen. Network grants produce no runtime configuration —
    network is denied by the absence of a host import, not by a setting.
    """
    return SandboxRuntimeConfig(
        memory_bytes=manifest.resources.memory_bytes,
        fuel=manifest.resources.fuel,
        wall_clock_ms=manifest.resources.wall_clock_ms,
        preopens=tuple(
            (grant.host_path, grant.guest_path, grant.write) for grant in manifest.filesystem
        ),
    )


def _require_wasm(import_module: Callable[[str], Any] = importlib.import_module) -> WasmRuntime:
    """Return the ``wasmtime`` runtime, or raise a clear install hint when it is absent."""
    try:
        return cast("WasmRuntime", import_module("wasmtime"))
    except ImportError as exc:
        raise RuntimeError(WASM_EXTRA_HINT) from exc


def _trip_epoch(engine: Any) -> None:
    """Advance the engine epoch past the deadline so a runaway tool is interrupted."""
    engine.increment_epoch()
    engine.increment_epoch()


def _trap_exit(wasm: WasmRuntime, trap: Any) -> str:
    """Map a wasmtime trap to a receipt exit token by its trap code."""
    code = getattr(trap, "trap_code", None)
    if code == wasm.TrapCode.OUT_OF_FUEL:
        return EXIT_OUT_OF_FUEL
    if code == wasm.TrapCode.INTERRUPT:
        return EXIT_EPOCH_DEADLINE
    return EXIT_ERROR


def _execute(
    wasm: WasmRuntime, config: SandboxRuntimeConfig, wasm_bytes: bytes, entrypoint: str
) -> tuple[str, bytes, str, int]:
    """Run the tool under the derived limits; return ``(exit, output, reason, fuel_used)``."""
    engine_config = wasm.Config()
    engine_config.consume_fuel = True
    engine_config.epoch_interruption = True
    engine = wasm.Engine(engine_config)
    store = wasm.Store(engine)
    store.set_fuel(config.fuel)
    store.set_limits(memory_size=config.memory_bytes)
    store.set_epoch_deadline(1)
    wasi = wasm.WasiConfig()
    for host, guest, write in config.preopens:
        dir_perms = wasm.DirPerms.READ_WRITE if write else wasm.DirPerms.READ_ONLY
        file_perms = wasm.FilePerms.READ_WRITE if write else wasm.FilePerms.READ_ONLY
        wasi.preopen_dir(host, guest, dir_perms, file_perms)
    store.set_wasi(wasi)
    linker = wasm.Linker(engine)
    linker.define_wasi()
    timer = threading.Timer(config.wall_clock_ms / 1000.0, _trip_epoch, args=(engine,))
    timer.daemon = True
    try:
        module = wasm.Module(engine, wasm_bytes)
        instance = linker.instantiate(store, module)
        func = instance.exports(store)[entrypoint]
        timer.start()
        result = func(store)
        output = b"" if result is None else str(result).encode()
        return EXIT_OK, output, "", config.fuel - store.get_fuel()
    except wasm.Trap as trap:
        return (
            _trap_exit(wasm, trap),
            b"",
            str(trap).splitlines()[0],
            config.fuel - store.get_fuel(),
        )
    except wasm.WasmtimeError as err:
        return EXIT_ERROR, b"", str(err).splitlines()[0], config.fuel - store.get_fuel()
    except KeyError:
        return EXIT_ERROR, b"", f"entrypoint '{entrypoint}' is not exported", config.fuel
    finally:
        timer.cancel()


def _inspect_module(wasm: WasmRuntime, wasm_bytes: bytes) -> tuple[bool, tuple[str, ...], str]:
    """Compile a module without instantiating it; return ``(valid, exported funcs, error)``.

    Compiling validates the module's structure and is cheap — it neither instantiates the
    module nor runs any of its code, so no fuel is spent and nothing the tool would do
    happens. Only the names of its exported *functions* are returned (memory, table, and
    global exports are not entrypoints). A malformed module yields ``(False, (), message)``.
    """
    engine = wasm.Engine(wasm.Config())
    try:
        module = wasm.Module(engine, wasm_bytes)
    except wasm.WasmtimeError as err:
        return False, (), str(err).splitlines()[0]
    names = tuple(
        sorted(
            str(export.name) for export in module.exports if isinstance(export.type, wasm.FuncType)
        )
    )
    return True, names, ""


def preflight_sandboxed(
    manifest: CapabilityManifest,
    wasm_bytes: bytes,
    *,
    entrypoint: str = DEFAULT_ENTRYPOINT,
    runtime: WasmRuntime | None = None,
) -> PreflightReport:
    """Pre-flight a tool against its manifest without running it — a cheap gate before a run.

    Loads ``wasm_bytes`` as a WebAssembly module (validating its structure) and reads its
    exported functions, but never instantiates or calls it: no fuel is spent and none of the
    tool's behaviour happens. The returned :class:`PreflightReport` says whether the module
    is well-formed, whether ``entrypoint`` is an exported function, whether the module
    matches its manifest digest, and what it would be granted — so an operator can confirm a
    tool is runnable (and is the module the manifest authorises) before ``run --approve``.

    The runtime is resolved lazily through the optional ``[wasm]`` extra; override
    ``runtime`` to inject a stand-in in tests.
    """
    wasm = runtime or _require_wasm()
    module_valid, exported_functions, compile_error = _inspect_module(wasm, wasm_bytes)
    return build_preflight_report(
        manifest=manifest,
        content_digest=digest_bytes(wasm_bytes),
        module_valid=module_valid,
        exported_functions=exported_functions,
        entrypoint=entrypoint,
        compile_error=compile_error,
    )


def run_sandboxed(
    manifest: CapabilityManifest,
    wasm_bytes: bytes,
    inputs: bytes,
    *,
    entrypoint: str = DEFAULT_ENTRYPOINT,
    runtime: WasmRuntime | None = None,
) -> RunReceipt:
    """Execute ``wasm_bytes`` under ``manifest``'s grants and return a bounded run receipt.

    The runtime is resolved lazily through the optional ``[wasm]`` extra (override
    ``runtime`` to inject a stand-in in tests). The tool is configured with the manifest's
    memory/fuel/wall-clock budget and preopened paths, then its ``entrypoint`` export is
    called. The receipt records how it exited (normal, out-of-fuel, wall-clock interrupt,
    or error), the fuel it burned, and digests of the input and output.
    """
    wasm = runtime or _require_wasm()
    config = derive_runtime_config(manifest)
    try:
        hardened = harden_preopens(config.preopens)
    except SandboxPathError as exc:
        # A grant whose host path resolves through a symlink, or is not a directory, is
        # refused before the tool runs — the sandbox never preopens a moving target.
        return build_run_receipt(
            manifest=manifest,
            inputs=inputs,
            output=b"",
            exit=EXIT_ERROR,
            fuel_used=0,
            reason=f"sandbox path refused: {exc}",
            preopened_paths=[],
        )
    exit_token, output, reason, fuel_used = _execute(
        wasm, replace(config, preopens=hardened), wasm_bytes, entrypoint
    )
    return build_run_receipt(
        manifest=manifest,
        inputs=inputs,
        output=output,
        exit=exit_token,
        fuel_used=fuel_used,
        reason=reason,
        preopened_paths=sorted(host for host, _guest, _write in hardened),
    )
