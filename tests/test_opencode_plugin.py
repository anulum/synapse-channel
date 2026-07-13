# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import pytest

from synapse_channel.opencode_plugin import PLUGIN_OWNER_MARKER, render_opencode_plugin


def test_plugin_is_argv_only_bounded_and_explicit_verdict_only() -> None:
    source = render_opencode_plugin(
        hook_argv=("/usr/bin/synapse", "adapters", "opencode-claim-hook", "--token-file", "/t"),
        timeout_seconds=7.2,
    )
    assert source.startswith(f"// {PLUGIN_OWNER_MARKER}\n")
    assert "Bun.spawn(HOOK_ARGV" in source
    assert "tool.execute.before" in source
    assert 'edit", "write", "apply_patch' in source
    assert "verdict?.allowed === true" in source
    assert "MAX_OUTPUT_BYTES = 65536" in source
    assert "TIMEOUT_MS = 7200" in source
    assert "shell" not in source


@pytest.mark.parametrize(
    "argv",
    [(), ("",), ("synapse", "--token", "secret")],
)
def test_plugin_rejects_unsafe_argv(argv: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        render_opencode_plugin(hook_argv=argv, timeout_seconds=5)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), 601])
def test_plugin_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValueError):
        render_opencode_plugin(hook_argv=("synapse",), timeout_seconds=timeout)


@pytest.mark.parametrize("limit", [0, 1023, 1_048_577])
def test_plugin_rejects_invalid_output_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        render_opencode_plugin(hook_argv=("synapse",), timeout_seconds=5, max_output_bytes=limit)
