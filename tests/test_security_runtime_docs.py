# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — security runtime documentation contract tests
"""Keep public security claims aligned with the production parser and runtime."""

from __future__ import annotations

import inspect
from pathlib import Path

from synapse_channel import cli
from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.multihub_serving import MultiHubServingPolicy
from synapse_channel.core.paranoid import MISSING_PARANOID_HOOKS
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION, negotiate_protocol_version

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    """Read one repository file as UTF-8 text."""
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _collapsed(relative_path: str) -> str:
    """Return lowercase single-spaced repository text."""
    return " ".join(_read(relative_path).lower().split())


def test_documented_security_flags_are_real_hub_parser_options() -> None:
    """Every activation flag in the evidence map must parse on the production CLI."""
    args = cli.build_parser().parse_args(
        [
            "hub",
            "--team-secure",
            "--identity-trust",
            "identity.json",
            "--role-grants",
            "roles.json",
            "--require-identity-binding",
            "--acl-policy",
            "acl.json",
            "--require-acl",
            "--message-auth-key",
            "key:fixture:project/agent",
            "--require-message-auth",
            "--federation-store",
            "federation.json",
            "--federation-observe-only",
            "--bridge-exposed",
            "--expect-multi-seat",
        ]
    )

    assert args.team_secure is True
    assert args.identity_trust == "identity.json"
    assert args.role_grants == "roles.json"
    assert args.require_identity_binding is True
    assert args.acl_policy == "acl.json"
    assert args.require_acl is True
    assert args.require_message_auth is True
    assert args.federation_store == "federation.json"
    assert args.federation_observe_only is True
    assert args.bridge_exposed is True
    assert args.expect_multi_seat is True

    default_hub = cli.build_parser().parse_args(["hub"])
    assert default_hub.bridge_exposed is False
    assert default_hub.expect_multi_seat is False

    security = _read("SECURITY.md")
    for option in (
        "--team-secure",
        "--paranoid",
        "--identity-trust",
        "--role-grants",
        "--require-identity-binding",
        "--acl-policy",
        "--require-acl",
        "--message-auth-key",
        "--require-message-auth",
        "--federation-store",
        "--federation-observe-only",
        "--db-key-file",
        "--bridge-exposed",
        "--expect-multi-seat",
    ):
        assert option in security


def test_federation_cli_and_docs_keep_operator_confirmation_explicit() -> None:
    """The shipped bundle workflow must remain explicit and deny automatic trust."""
    parsed = cli.build_parser().parse_args(["federation", "list"])
    assert parsed.command == "federation"
    assert parsed.federation_command == "list"

    federation = _collapsed("docs/federated-trust-model.md")
    for command in (
        "synapse federation offer",
        "synapse federation fetch",
        "synapse federation import",
        "synapse federation list",
        "synapse federation rotate",
        "synapse federation revoke",
    ):
        assert command in federation
    assert "out-of-band by design" in federation
    assert "performs no automatic trust discovery" in federation
    assert "not a certificate authority" in federation


def test_public_claims_separate_shipped_controls_from_staged_work() -> None:
    """Known stale claims must stay removed while shipped profiles stay explicit."""
    combined = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "SECURITY.md",
            "docs/protocol.md",
            "docs/identity-and-acl.md",
            "docs/federated-trust-model.md",
            "docs/glossary.md",
            "docs/public-surface.md",
        )
    )
    collapsed = " ".join(combined.lower().split())

    for stale_claim in (
        "the planned [identity and acl]",
        "the planned [federated trust model]",
        "the planned [signed events and mtls]",
        "the planned [agent trust graph]",
        "it is a design, not an implementation",
        "federation layer that joins domains does not exist yet",
        "it is not implemented as a cli flag yet",
        "this is a design target, not implemented yet. identity and acls",
        "each such page states that it is not implemented",
    ):
        assert stale_claim not in collapsed

    assert "signed capability cards are implemented as advisory tamper evidence" in _collapsed(
        "docs/signed-capability-cards.md"
    )
    assert "they are not implemented yet" in _collapsed("docs/differential-privacy-blackboard.md")
    assert "not externally validated for full a2a conformance" in collapsed
    assert " d1 " not in f" {collapsed} "


def test_wire_version_docs_match_negotiate_down_runtime() -> None:
    """Wire documentation must describe the version-two compatibility decision."""
    assert WIRE_PROTOCOL_VERSION == 2
    assert negotiate_protocol_version(1).effective_version == 1
    assert negotiate_protocol_version(3).effective_version == 2
    assert negotiate_protocol_version(None).effective_version == 1
    assert negotiate_protocol_version(1).warning is not None
    assert negotiate_protocol_version(3).warning is not None
    assert negotiate_protocol_version(None).warning is not None

    protocol_doc = _collapsed("docs/protocol.md")
    protocol_source = _collapsed("src/synapse_channel/core/protocol.py")
    assert "current wire is version `2`" in protocol_doc
    assert "lowest common wire version" in protocol_doc
    assert "advertise-only for now" not in protocol_source
    assert "multi-hub network fetcher records that decision" in protocol_source


def test_federation_expiry_is_documented_as_wall_clock_epoch_time() -> None:
    """Persisted peering expiry must never be described as process uptime."""
    policy_doc = inspect.getdoc(MultiHubServingPolicy) or ""
    peer_doc = inspect.getdoc(FederationPeer) or ""
    federation_doc = _collapsed("docs/federated-trust-model.md")

    assert "POSIX wall-clock time" in policy_doc
    assert "current monotonic time" not in policy_doc
    assert "UNIX epoch time" in peer_doc
    assert "posix wall-clock epoch seconds" in federation_doc
    assert "never with process-relative monotonic time" in federation_doc


def test_paranoid_report_qualifies_separately_available_controls() -> None:
    """The strict profile must not report shipped opt-ins as absent features."""
    joined = " ".join(MISSING_PARANOID_HOOKS)
    assert "at-rest encryption (available separately; not enabled by --paranoid)" in joined
    assert "private channels (available separately; not enabled by --paranoid)" in joined
    assert "cryptographic per-agent identity verification (compose --team-secure)" in joined
    assert "signed-event trust loading" in joined
