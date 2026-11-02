# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode config edits refuse oversized and foreign shapes
"""Install and uninstall plans never rewrite a config they do not fully own."""

from __future__ import annotations

import json

import pytest

from synapse_channel.opencode_adapter import (
    ADAPTER_OWNER,
    MAX_CONFIG_BYTES,
    OpenCodeAdapterError,
    parse_config,
    plan_config_install,
    plan_config_uninstall,
)

OWNED = {"type": "local", "environment": {"SYNAPSE_ADAPTER_OWNER": ADAPTER_OWNER}}


def test_config_over_the_size_limit_is_refused_before_parsing() -> None:
    """A config larger than the automatic-edit limit is never decoded or rewritten."""
    oversized = '{"padding": "' + "x" * MAX_CONFIG_BYTES + '"}'
    with pytest.raises(OpenCodeAdapterError, match="exceeds the automatic-edit size limit"):
        parse_config(oversized)


@pytest.mark.parametrize("current", ["not-an-object", ["list"], {"environment": "flat"}])
def test_install_refuses_an_unowned_entry_of_any_shape(current: object) -> None:
    """A non-object entry, or one without the owner marker, is never overwritten."""
    existing = json.dumps({"mcp": {"synapse": current}})
    with pytest.raises(OpenCodeAdapterError, match="exists but is not Synapse-owned"):
        plan_config_install(existing, dict(OWNED))


def test_uninstall_refuses_a_non_object_mcp_field() -> None:
    """An 'mcp' field that is not an object is reported, not deleted or replaced."""
    with pytest.raises(OpenCodeAdapterError, match="field 'mcp' must be an object"):
        plan_config_uninstall(json.dumps({"mcp": ["synapse"]}))


def test_uninstall_refuses_a_foreign_entry_and_removes_an_owned_one() -> None:
    """Only the owner-marked entry is removed; a user's own entry stays untouched."""
    foreign = json.dumps({"mcp": {"synapse": {"type": "local", "command": ["mine"]}}})
    with pytest.raises(OpenCodeAdapterError, match="is not Synapse-owned; refusing to remove"):
        plan_config_uninstall(foreign)
    owned = json.dumps({"mcp": {"synapse": OWNED, "other": {"type": "remote"}}})
    remaining = plan_config_uninstall(owned)
    assert remaining is not None
    assert json.loads(remaining) == {"mcp": {"other": {"type": "remote"}}}
