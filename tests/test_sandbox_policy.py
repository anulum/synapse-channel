# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — sandbox capability-manifest policy regressions

from __future__ import annotations

import pytest

from synapse_channel.core.acl import (
    SANDBOX,
    WOULD_ALLOW,
    WOULD_DENY,
    AclPolicy,
    Target,
    evaluate_access,
)
from synapse_channel.core.sandbox_policy import (
    AUTHORISED,
    DEFAULT_RESOURCE_GRANT,
    SANDBOX_TARGET_FS,
    SANDBOX_TARGET_NET,
    CapabilityManifest,
    FilesystemGrant,
    NetworkGrant,
    ResourceGrant,
    SandboxDecision,
    SandboxDenyReason,
    SandboxManifestError,
    SandboxRequest,
    authorise,
    manifest_from_dict,
    to_acl_rules,
)

_DIGEST = "sha256:" + "a" * 64
_MANIFEST = CapabilityManifest(
    tool_id="formatter",
    content_digest=_DIGEST,
    filesystem=(
        FilesystemGrant("/host/in", "/in", write=False),
        FilesystemGrant("/host/out", "/out", write=True),
    ),
    network=(NetworkGrant("api.internal", 443),),
    resources=ResourceGrant(memory_bytes=2048, fuel=5000, wall_clock_ms=200),
    namespace="SYNAPSE-CHANNEL",
)


def _request(**overrides: object) -> SandboxRequest:
    base: dict[str, object] = {"tool_id": "formatter", "content_digest": _DIGEST}
    base.update(overrides)
    return SandboxRequest(**base)  # type: ignore[arg-type]


def test_perms_reflects_write_flag() -> None:
    assert FilesystemGrant("/h", "/g").perms() == "read"
    assert FilesystemGrant("/h", "/g", write=True).perms() == "read_write"


def test_authorise_allows_a_request_within_every_grant() -> None:
    decision = authorise(
        _MANIFEST,
        _request(
            filesystem=(("/in/data.csv", False), ("/out/result.txt", True)),
            network=(("api.internal", 443),),
            memory_bytes=2048,
            fuel=5000,
            wall_clock_ms=200,
        ),
    )
    assert decision.allowed is True
    assert decision.reason == AUTHORISED
    assert decision.granted is _MANIFEST


def test_authorise_denies_a_swapped_module() -> None:
    decision = authorise(_MANIFEST, _request(content_digest="sha256:" + "b" * 64))
    assert decision.allowed is False
    assert decision.reason == SandboxDenyReason.DIGEST_MISMATCH
    assert decision.granted is None


def test_authorise_denies_an_ungranted_path() -> None:
    decision = authorise(_MANIFEST, _request(filesystem=(("/etc/passwd", False),)))
    assert decision.reason == SandboxDenyReason.FILESYSTEM_NOT_GRANTED


def test_authorise_denies_a_write_to_a_read_only_grant() -> None:
    decision = authorise(_MANIFEST, _request(filesystem=(("/in/data.csv", True),)))
    assert decision.reason == SandboxDenyReason.WRITE_NOT_GRANTED


def test_authorise_allows_a_write_covered_by_any_writable_grant() -> None:
    manifest = CapabilityManifest(
        tool_id="t",
        content_digest=_DIGEST,
        filesystem=(
            FilesystemGrant("/h1", "/shared", write=False),
            FilesystemGrant("/h2", "/shared", write=True),
        ),
    )
    decision = authorise(manifest, _request(tool_id="t", filesystem=(("/shared/x", True),)))
    assert decision.allowed is True


def test_authorise_denies_an_ungranted_endpoint() -> None:
    decision = authorise(_MANIFEST, _request(network=(("evil.example", 80),)))
    assert decision.reason == SandboxDenyReason.NETWORK_NOT_GRANTED


def test_authorise_denies_each_over_budget_resource() -> None:
    assert (
        authorise(_MANIFEST, _request(memory_bytes=4096)).reason
        == SandboxDenyReason.MEMORY_EXCEEDS_GRANT
    )
    assert authorise(_MANIFEST, _request(fuel=9999)).reason == SandboxDenyReason.FUEL_EXCEEDS_GRANT
    assert (
        authorise(_MANIFEST, _request(wall_clock_ms=10_000)).reason
        == SandboxDenyReason.WALLCLOCK_EXCEEDS_GRANT
    )


def test_under_matches_exact_and_subtree_only() -> None:
    # a request exactly at the preopen root and one nested below it are both covered;
    # a sibling that merely shares a prefix is not
    covered = authorise(_MANIFEST, _request(filesystem=(("/in", False), ("/in/deep/x", False))))
    assert covered.allowed is True
    sibling = authorise(_MANIFEST, _request(filesystem=(("/india", False),)))
    assert sibling.reason == SandboxDenyReason.FILESYSTEM_NOT_GRANTED


def test_manifest_and_decision_serialise() -> None:
    payload = _MANIFEST.to_dict()
    assert payload["tool_id"] == "formatter"
    assert payload["filesystem"][1] == {
        "host_path": "/host/out",
        "guest_path": "/out",
        "write": True,
    }
    assert payload["network"] == [{"host": "api.internal", "port": 443}]
    assert payload["resources"]["fuel"] == 5000

    allowed = authorise(_MANIFEST, _request()).to_dict()
    assert allowed["granted"]["tool_id"] == "formatter"
    denied = SandboxDecision(False, "t", SandboxDenyReason.DIGEST_MISMATCH).to_dict()
    assert denied["granted"] is None


def test_to_acl_rules_round_trips_through_evaluate_access() -> None:
    policy = AclPolicy(rules=to_acl_rules(_MANIFEST))
    assert len(policy.rules) == 3  # two filesystem + one network, no resource rule

    def decide(kind: str, value: str) -> str:
        return evaluate_access(
            subject="formatter",
            project="SYNAPSE-CHANNEL",
            permission=SANDBOX,
            target=Target(kind, value),
            policy=policy,
        ).decision

    assert decide(SANDBOX_TARGET_FS, "/in") == WOULD_ALLOW
    assert decide(SANDBOX_TARGET_NET, "api.internal:443") == WOULD_ALLOW
    assert decide(SANDBOX_TARGET_FS, "/etc") == WOULD_DENY


def test_to_acl_rules_is_empty_for_a_no_capability_manifest() -> None:
    assert to_acl_rules(CapabilityManifest("t", _DIGEST)) == []


def test_manifest_from_dict_round_trips_a_full_manifest() -> None:
    parsed = manifest_from_dict(_MANIFEST.to_dict())
    assert parsed == _MANIFEST


def test_manifest_from_dict_defaults_to_deny_by_default() -> None:
    parsed = manifest_from_dict({"tool_id": "bare", "content_digest": _DIGEST})
    assert parsed.filesystem == () and parsed.network == ()
    assert parsed.resources == DEFAULT_RESOURCE_GRANT
    assert parsed.namespace == ""


def test_manifest_from_dict_rejects_structural_errors() -> None:
    with pytest.raises(SandboxManifestError, match="must be a mapping"):
        manifest_from_dict(["nope"])
    with pytest.raises(SandboxManifestError, match="non-empty 'tool_id'"):
        manifest_from_dict({"content_digest": _DIGEST})
    with pytest.raises(SandboxManifestError, match="non-empty 'content_digest'"):
        manifest_from_dict({"tool_id": "t"})
    with pytest.raises(SandboxManifestError, match="'sha256:' digest"):
        manifest_from_dict({"tool_id": "t", "content_digest": "md5:bad"})
    with pytest.raises(SandboxManifestError, match="'filesystem' must be a list"):
        manifest_from_dict({"tool_id": "t", "content_digest": _DIGEST, "filesystem": {}})


def test_manifest_from_dict_rejects_malformed_grants() -> None:
    base = {"tool_id": "t", "content_digest": _DIGEST}
    with pytest.raises(SandboxManifestError, match="filesystem grant must be a mapping"):
        manifest_from_dict({**base, "filesystem": ["nope"]})
    with pytest.raises(SandboxManifestError, match="non-empty 'guest_path'"):
        manifest_from_dict({**base, "filesystem": [{"host_path": "/h"}]})
    with pytest.raises(SandboxManifestError, match="network grant must be a mapping"):
        manifest_from_dict({**base, "network": ["nope"]})
    with pytest.raises(SandboxManifestError, match="'port' must be an integer"):
        manifest_from_dict({**base, "network": [{"host": "h", "port": "443"}]})
    with pytest.raises(SandboxManifestError, match="'port' must be an integer"):
        manifest_from_dict({**base, "network": [{"host": "h", "port": True}]})


def test_manifest_from_dict_validates_resources() -> None:
    base = {"tool_id": "t", "content_digest": _DIGEST}
    with pytest.raises(SandboxManifestError, match="'resources' must be a mapping"):
        manifest_from_dict({**base, "resources": 5})
    with pytest.raises(SandboxManifestError, match="'memory_bytes' must be a positive integer"):
        manifest_from_dict({**base, "resources": {"memory_bytes": 0}})
    with pytest.raises(SandboxManifestError, match="'fuel' must be a positive integer"):
        manifest_from_dict({**base, "resources": {"fuel": True}})
    custom = manifest_from_dict({**base, "resources": {"memory_bytes": 99}})
    assert custom.resources.memory_bytes == 99  # other fields fall back to the default budget
    assert custom.resources.fuel == DEFAULT_RESOURCE_GRANT.fuel
