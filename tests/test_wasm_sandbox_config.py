# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure sandbox runtime-config derivation, receipt, and import-guard tests

from __future__ import annotations

import pytest

from synapse_channel.core.sandbox_policy import (
    CapabilityManifest,
    FilesystemGrant,
    NetworkGrant,
    ResourceGrant,
)
from synapse_channel.core.sandbox_receipt import (
    EXIT_OK,
    build_run_receipt,
    digest_bytes,
    granted_capabilities,
)
from synapse_channel.core.wasm_sandbox import (
    WASM_EXTRA_HINT,
    SandboxRuntimeConfig,
    _require_wasm,
    derive_runtime_config,
)

_MANIFEST = CapabilityManifest(
    tool_id="formatter",
    content_digest="sha256:" + "a" * 64,
    filesystem=(
        FilesystemGrant("/host/in", "/in", write=False),
        FilesystemGrant("/host/out", "/out", write=True),
    ),
    network=(NetworkGrant("api.internal", 443),),
    resources=ResourceGrant(memory_bytes=2048, fuel=5000, wall_clock_ms=200),
)


def test_derive_runtime_config_maps_resources_and_preopens() -> None:
    config = derive_runtime_config(_MANIFEST)
    assert isinstance(config, SandboxRuntimeConfig)
    assert (config.memory_bytes, config.fuel, config.wall_clock_ms) == (2048, 5000, 200)
    # each filesystem grant becomes a (host, guest, write) preopen; network produces none
    assert config.preopens == (("/host/in", "/in", False), ("/host/out", "/out", True))


def test_require_wasm_returns_the_module_when_present() -> None:
    sentinel = object()
    assert _require_wasm(import_module=lambda _name: sentinel) is sentinel


def test_require_wasm_raises_the_install_hint_when_absent() -> None:
    def _missing(_name: str) -> object:
        raise ImportError("no wasmtime here")

    with pytest.raises(RuntimeError, match=r"synapse-channel\[wasm\]") as excinfo:
        _require_wasm(import_module=_missing)
    assert str(excinfo.value) == WASM_EXTRA_HINT


def test_digest_bytes_is_a_stable_sha256() -> None:
    assert (
        digest_bytes(b"")
        == "sha256:" + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert digest_bytes(b"abc").startswith("sha256:")
    assert digest_bytes(b"abc") != digest_bytes(b"xyz")


def test_granted_capabilities_render_sorted_and_bounded() -> None:
    caps = granted_capabilities(_MANIFEST)
    assert caps == sorted(caps)
    assert "fs:/in:read" in caps
    assert "fs:/out:read_write" in caps
    assert "net:api.internal:443" in caps
    assert any(cap.startswith("resource:mem=2048,fuel=5000,wall=200ms") for cap in caps)


def test_build_run_receipt_carries_digests_and_outcome() -> None:
    receipt = build_run_receipt(
        manifest=_MANIFEST, inputs=b"in", output=b"out", exit=EXIT_OK, fuel_used=17
    )
    assert receipt["tool_id"] == "formatter"
    assert receipt["content_digest"] == _MANIFEST.content_digest
    assert receipt["inputs_digest"] == digest_bytes(b"in")
    assert receipt["output_digest"] == digest_bytes(b"out")
    assert receipt["exit"] == EXIT_OK and receipt["fuel_used"] == 17
    assert receipt["reason"] == ""
    assert receipt["granted_capabilities"] == granted_capabilities(_MANIFEST)
