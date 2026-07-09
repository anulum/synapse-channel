# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests pinning the package's public export surface (__all__)

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

import synapse_channel

_FROZEN_PUBLIC_API = (
    "DEFAULT_HUB_URI",
    "HUB_URI_ENV_VAR",
    "MEMORY_KINDS",
    "PRIORITY_SENDERS",
    "Blackboard",
    "CapabilityCard",
    "CapabilityContract",
    "CapabilityRegistry",
    "ChatBackend",
    "ClaimStatus",
    "CompactionResult",
    "Decision",
    "EventStore",
    "EvidenceKind",
    "FederationConfig",
    "Finding",
    "Freshness",
    "HubAuthConfig",
    "HubConfig",
    "HubLimits",
    "HubMetricsConfig",
    "Intervention",
    "LedgerTask",
    "Lifecycle",
    "MessageType",
    "Metric",
    "MultiHubConfig",
    "OpenAIChatClient",
    "ProgressNote",
    "ResourceOffer",
    "RetentionPolicy",
    "RuleBasedClient",
    "Subkind",
    "StallPolicy",
    "SupervisorWorker",
    "SynapseAgent",
    "SynapseHub",
    "TakeoverDamping",
    "SynapseLLMWorker",
    "SynapseState",
    "TaskClaim",
    "TaskClass",
    "TaskStatus",
    "TieredChatClient",
    "TokenAuthenticator",
    "__version__",
    "addresses_project",
    "admit",
    "build_envelope",
    "can_transition",
    "classify",
    "collect_hub_metrics",
    "compact",
    "decode_lite",
    "default_hub_uri",
    "detect_stalls",
    "encode_lite",
    "health_snapshot",
    "is_directed",
    "is_recipient",
    "is_service_message",
    "paths_overlap",
    "plan_team",
    "render_prometheus",
    "run_team",
    "sanitize_text",
    "scopes_conflict",
    "system_message",
    "wakes",
    "would_create_cycle",
)


def test_public_api_exports_are_exactly_frozen() -> None:
    """Pin every exported package name, not only the export count."""
    assert tuple(synapse_channel.__all__) == _FROZEN_PUBLIC_API


def test_every_all_name_is_importable() -> None:
    # A name promised by __all__ that no longer resolves is a broken public surface.
    missing = [name for name in synapse_channel.__all__ if not hasattr(synapse_channel, name)]
    assert not missing, f"names in __all__ but not importable: {missing}"


def test_all_has_no_duplicates() -> None:
    assert len(synapse_channel.__all__) == len(set(synapse_channel.__all__))


def test_no_private_helpers_leak_into_the_public_surface() -> None:
    # Single-underscore internals must not be re-exported; __version__ (dunder) is exempt.
    leaked = [
        name
        for name in synapse_channel.__all__
        if name.startswith("_") and not name.startswith("__")
    ]
    assert not leaked, f"private names leaked into __all__: {leaked}"


def test_version_is_exported_and_nonempty() -> None:
    assert "__version__" in synapse_channel.__all__
    assert isinstance(synapse_channel.__version__, str)
    assert synapse_channel.__version__.strip()


def test_exports_map_covers_exactly_the_public_surface() -> None:
    # The PEP 562 map and __all__ must stay in lockstep: a name in one but not
    # the other is either an unreachable promise or an unadvertised export.
    assert set(synapse_channel._EXPORTS) == set(synapse_channel.__all__) - {"__version__"}


def test_every_lazy_name_resolves_to_its_declared_origin() -> None:
    # Accessing each name walks the lazy path (or its cache) and must yield the
    # very object living in the module the map points at — not a copy.
    for name, target in synapse_channel._EXPORTS.items():
        module_name, _, attribute = target.partition(":")
        origin = getattr(importlib.import_module(module_name), attribute)
        assert getattr(synapse_channel, name) is origin, name


def test_lazy_access_caches_the_resolved_name_on_the_package() -> None:
    synapse_channel.__dict__.pop("paths_overlap", None)
    assert "paths_overlap" not in vars(synapse_channel)
    resolved = synapse_channel.paths_overlap
    assert vars(synapse_channel)["paths_overlap"] is resolved


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'definitely_missing'"):
        _ = synapse_channel.definitely_missing


def test_dir_lists_the_full_public_surface() -> None:
    assert set(synapse_channel.__all__) <= set(dir(synapse_channel))


def test_bare_package_import_does_not_drag_the_heavy_stack() -> None:
    # The point of the lazy facade: `import synapse_channel` alone must not
    # pull in the WebSocket/asyncio client chain or the hub.
    probe = (
        "import sys, synapse_channel\n"
        "heavy = [m for m in ('websockets', 'synapse_channel.client.agent',"
        " 'synapse_channel.core.hub') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
        "print(synapse_channel.__version__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == synapse_channel.__version__
