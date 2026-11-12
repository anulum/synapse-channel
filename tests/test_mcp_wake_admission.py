# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator grant notification revocation tests
"""Exercise real private policy reloads and fixed delivery scope bindings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_http_helpers import write_policy
from synapse_channel.mcp.http_config import HttpAuthConfig, load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_pointer import WakeLedgerError


def _policy(path: Path) -> HttpAuthConfig:
    write_policy(path, Ed25519PrivateKey.generate())
    payload = load_http_auth_config(path).model_dump(mode="json")
    grant = payload["subjects"]["alice"]["projects"]["ALPHA"]
    grant["tools"].extend(["synapse_inbox_page", "synapse_owned_work"])
    grant["wake"] = {
        "url": "https://receiver.example.test/hook",
        "signing_key_file": str(path.parent / "signing"),
    }
    path.write_text(json.dumps(payload))
    return load_http_auth_config(path)


def test_exact_scope_is_stable_and_rotation_does_not_rebind(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    config = _policy(path)
    admission = WakeAdmission(path, "alice", "ALPHA", config)
    original = admission.scope("hub-1")
    assert original.seat == "ALPHA/alice"
    assert admission.check() == "enabled"
    assert admission.endpoint.url == "https://receiver.example.test/hook"
    secret = Path(admission.endpoint.signing_key_file)
    secret.write_text("synthetic-rotation-value")
    secret.chmod(0o600)
    reopened = WakeAdmission(path, "alice", "ALPHA", load_http_auth_config(path))
    assert reopened.scope("hub-1") == original
    assert reopened.scope("hub-2").key == original.key
    payload = config.model_dump(mode="json")
    payload["subjects"]["alice"]["projects"]["ALPHA"]["tools"].reverse()
    path.write_text(json.dumps(payload))
    assert (
        WakeAdmission(path, "alice", "ALPHA", load_http_auth_config(path)).binding
        == original.binding
    )
    assert "receiver.example.test" not in repr(admission.endpoint)
    assert "signing" not in repr(admission.endpoint)


@pytest.mark.parametrize(
    "case", ["subject", "disabled", "project", "target", "inbox", "owned-work"]
)
def test_unprovisioned_notification_is_refused(tmp_path: Path, case: str) -> None:
    path = tmp_path / "policy.json"
    config = _policy(path)
    payload = config.model_dump(mode="json")
    subject, project = "alice", "ALPHA"
    if case == "subject":
        subject = "absent"
    elif case == "project":
        project = "ABSENT"
    elif case == "disabled":
        payload["subjects"]["alice"]["enabled"] = False
    elif case == "target":
        payload["subjects"]["alice"]["projects"]["ALPHA"]["wake"] = None
    else:
        payload["subjects"]["alice"]["projects"]["ALPHA"]["tools"].remove(
            "synapse_owned_work" if case == "owned-work" else "synapse_inbox_page"
        )
    path.write_text(json.dumps(payload))
    with pytest.raises(WakeLedgerError, match="wake delivery is not authorized"):
        WakeAdmission(path, subject, project, load_http_auth_config(path))


@pytest.mark.parametrize("case", ["missing", "disabled"])
def test_live_subject_revocation_refuses_existing_scope(tmp_path: Path, case: str) -> None:
    path = tmp_path / "policy.json"
    config = _policy(path)
    admission = WakeAdmission(path, "alice", "ALPHA", config)
    payload = config.model_dump(mode="json")
    if case == "missing":
        payload["subjects"]["other"] = payload["subjects"].pop("alice")
    else:
        payload["subjects"]["alice"]["enabled"] = False
    path.write_text(json.dumps(payload))
    assert admission.check() == "disabled"
    with pytest.raises(WakeLedgerError, match="wake delivery is not authorized"):
        admission.scope("hub-1")


@pytest.mark.parametrize(
    "case", ["issuer", "resource", "endpoint", "keypath", "identity", "tools", "project"]
)
def test_live_authority_change_requires_explicit_reprovision(tmp_path: Path, case: str) -> None:
    path = tmp_path / "policy.json"
    config = _policy(path)
    admission = WakeAdmission(path, "alice", "ALPHA", config)
    payload = config.model_dump(mode="json")
    grant = payload["subjects"]["alice"]["projects"]["ALPHA"]
    if case in {"issuer", "resource"}:
        payload[case] = "https://changed.example.test/" + case
    elif case == "endpoint":
        grant["wake"]["url"] = "https://changed.example.test/hook"
    elif case == "keypath":
        grant["wake"]["signing_key_file"] = str(tmp_path / "other-key")
    elif case == "identity":
        grant["identity_key_id"] = "replacement-native-key"
    elif case == "tools":
        grant["tools"].append("synapse_send")
    else:
        grant["seat"] = "BETA/alice"
        grant["task_prefix"] = "BETA/"
        payload["subjects"]["alice"]["projects"]["BETA"] = payload["subjects"]["alice"][
            "projects"
        ].pop("ALPHA")
    path.write_text(json.dumps(payload))
    assert admission.check() == "grant_changed"
    with pytest.raises(WakeLedgerError, match="wake delivery is not authorized"):
        admission.scope("hub-1")
    if case != "project":
        new = WakeAdmission(path, "alice", "ALPHA", load_http_auth_config(path))
        assert new.binding != admission.binding


def test_unreadable_current_policy_never_reuses_cached_authority(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    admission = WakeAdmission(path, "alice", "ALPHA", _policy(path))
    path.write_text('{"subjects":"private-sensitive-invalid-config"}')
    assert admission.check() == "unavailable"
    with pytest.raises(WakeLedgerError) as failure:
        admission.scope("hub-1")
    assert str(failure.value) == "wake delivery is not authorized"
    assert "private-sensitive" not in str(failure.value)
