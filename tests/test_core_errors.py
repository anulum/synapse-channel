# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — error-taxonomy base, frozen code registry, and drift gate
"""The error taxonomy: base behaviour, the frozen code registry, and drift.

Three layers of protection:

1. **Base contract** — a subclass must declare its own snake_case ``code`` at
   class-definition time, and :func:`~synapse_channel.core.errors.error_code`
   classifies foreign exceptions as ``""``.
2. **Frozen registry** — the full class-to-code map is pinned here the same
   way the wire-surface freeze pins message fields: a released code never
   changes meaning and is never reused, so renaming or re-coding a class must
   consciously edit this file.
3. **Drift gate** — an AST sweep over ``src/`` (no imports, so optional
   dependencies cannot mask it) refuses any ``*Error`` class that does not
   join the taxonomy, keeping future modules from quietly regressing to bare
   built-in exceptions.

Legacy compatibility is asserted per class: every re-based exception is still
an instance of its historical built-in base, so pre-existing ``except``
clauses keep catching exactly what they caught before the taxonomy landed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from synapse_channel.core.errors import SynapseError, error_code

_SRC = Path(__file__).resolve().parent.parent / "src" / "synapse_channel"

# ---------------------------------------------------------------------------
# The frozen registry: class name -> (module, code, legacy base).
# A row may be ADDED for a new error class; an existing row must never change.
# ---------------------------------------------------------------------------
FROZEN_ERROR_CODES: dict[str, tuple[str, str, type[BaseException]]] = {
    "A2AConflictError": (
        "synapse_channel.a2a_errors",
        "a2a_conflict",
        ValueError,
    ),
    "A2AError": ("synapse_channel.a2a_errors", "a2a", ValueError),
    "A2AInteropTraceError": (
        "synapse_channel.a2a_interop_trace",
        "a2a_interop_trace",
        RuntimeError,
    ),
    "A2ANotFoundError": (
        "synapse_channel.a2a_errors",
        "a2a_not_found",
        ValueError,
    ),
    "A2AQuotaError": ("synapse_channel.a2a_errors", "a2a_quota", ValueError),
    "A2AStoreError": ("synapse_channel.a2a_errors", "a2a_store", ValueError),
    "A2AValidationError": (
        "synapse_channel.a2a_errors",
        "a2a_validation",
        ValueError,
    ),
    "AclError": ("synapse_channel.core.acl", "acl", ValueError),
    "ApplyPatchPathError": ("synapse_channel.apply_patch_paths", "apply_patch_path", ValueError),
    "AutoActionStoreError": (
        "synapse_channel.participants.auto_action_store",
        "auto_action_store",
        Exception,
    ),
    "BoundedReadError": ("synapse_channel.core.http_response", "bounded_read", ValueError),
    "CapabilityCardSigningError": (
        "synapse_channel.core.capability_card_signing",
        "capability_card_signing",
        ValueError,
    ),
    "CapabilityCardTrustError": (
        "synapse_channel.core.capability_card_trust",
        "capability_card_trust",
        ValueError,
    ),
    "ClaimCoverageError": (
        "synapse_channel.git.claim_coverage",
        "claim_coverage",
        RuntimeError,
    ),
    "ClaimCheckConfigError": (
        "synapse_channel.git.claim_check_context",
        "claim_check_config",
        RuntimeError,
    ),
    "ClaimForwardError": (
        "synapse_channel.core.multihub_claim_transport",
        "claim_forward",
        RuntimeError,
    ),
    "ClaimForwardTimeoutError": (
        "synapse_channel.core.multihub_claim_transport",
        "claim_forward_timeout",
        RuntimeError,
    ),
    "ClaimGuardError": (
        "synapse_channel.claude_claim_guard",
        "claude_claim_guard",
        RuntimeError,
    ),
    "ClaimStateError": (
        "synapse_channel.claim_state",
        "claim_state",
        RuntimeError,
    ),
    "ClaimWireError": ("synapse_channel.core.multihub_claim_wire", "claim_wire", ValueError),
    "CodexClaimGuardError": (
        "synapse_channel.codex_claim_guard",
        "codex_claim_guard",
        RuntimeError,
    ),
    "DeadLetterForwardError": (
        "synapse_channel.core.dead_letter_forwarding",
        "dead_letter_forward",
        RuntimeError,
    ),
    "DeadLetterForwardingWireError": (
        "synapse_channel.core.dead_letter_forwarding",
        "dead_letter_forwarding_wire",
        ValueError,
    ),
    "DeliberationError": (
        "synapse_channel.core.deliberation",
        "deliberation",
        ValueError,
    ),
    "FederationDoctorError": (
        "synapse_channel.cli_doctor_federation",
        "federation_doctor",
        RuntimeError,
    ),
    "FederationFetchError": (
        "synapse_channel.core.federation_fetch",
        "federation_fetch",
        RuntimeError,
    ),
    "FederationRotationError": (
        "synapse_channel.core.federation_rotation",
        "federation_rotation",
        ValueError,
    ),
    "FederationStoreError": (
        "synapse_channel.core.federation_store",
        "federation_store",
        ValueError,
    ),
    "FederationWireError": ("synapse_channel.core.federation_wire", "federation_wire", ValueError),
    "FileClaimGuardError": (
        "synapse_channel.file_claim_guard",
        "file_claim_guard",
        RuntimeError,
    ),
    "GeminiClaimGuardError": (
        "synapse_channel.gemini_claim_guard",
        "gemini_claim_guard",
        RuntimeError,
    ),
    "GitError": ("synapse_channel.git.gitclaim", "git", RuntimeError),
    "GrokClaimGuardError": (
        "synapse_channel.grok_claim_guard",
        "grok_claim_guard",
        RuntimeError,
    ),
    "HubTLSConfigError": ("synapse_channel.core.tls", "hub_tls_config", ValueError),
    "IdentityBindingError": (
        "synapse_channel.core.identity_binding",
        "identity_binding",
        ValueError,
    ),
    "IdentityError": ("synapse_channel.core.identity", "identity", ValueError),
    "IdentityKeyError": ("synapse_channel.core.identity_keys", "identity_key", ValueError),
    "InsecureBindError": ("synapse_channel.core.hub_exposure", "insecure_bind", RuntimeError),
    "KimiClaimGuardError": (
        "synapse_channel.kimi_claim_guard",
        "kimi_claim_guard",
        RuntimeError,
    ),
    "KimiHookConfigFileError": (
        "synapse_channel.kimi_hook_config_file",
        "kimi_hook_config_file",
        ValueError,
    ),
    "KimiHookInstallerError": (
        "synapse_channel.kimi_hook_installer",
        "kimi_hook_installer",
        ValueError,
    ),
    "McpAccessError": ("synapse_channel.core.mcp_outbound", "mcp_access", PermissionError),
    "McpConfigError": ("synapse_channel.core.mcp_outbound", "mcp_config", ValueError),
    "McpGitClaimError": ("synapse_channel.mcp.git_claim", "mcp_git_claim", RuntimeError),
    "McpToolError": ("synapse_channel.core.mcp_outbound", "mcp_tool", RuntimeError),
    "MemoryRecallInputError": (
        "synapse_channel.core.memory_projection",
        "memory_recall_input",
        ValueError,
    ),
    "MultiHubFetchError": (
        "synapse_channel.core.multihub_transport",
        "multihub_fetch",
        RuntimeError,
    ),
    "MultiHubWireError": ("synapse_channel.core.multihub_wire", "multihub_wire", ValueError),
    "OpenCodeAdapterError": ("synapse_channel.opencode_adapter", "opencode_adapter", ValueError),
    "OpenCodeAdapterFileError": (
        "synapse_channel.opencode_adapter_files",
        "opencode_adapter_file",
        OSError,
    ),
    "OpenCodeApiError": ("synapse_channel.participants.opencode_api", "opencode_api", RuntimeError),
    "OpenCodeAuthError": (
        "synapse_channel.participants.opencode_auth",
        "opencode_auth",
        ValueError,
    ),
    "OpenCodeClaimGuardError": (
        "synapse_channel.opencode_claim_guard",
        "opencode_claim_guard",
        RuntimeError,
    ),
    "ParanoidModeError": ("synapse_channel.core.paranoid", "paranoid_mode", ValueError),
    "PayloadCryptoError": ("synapse_channel.core.payload_crypto", "payload_crypto", ValueError),
    "PathResolutionError": (
        "synapse_channel.path_resolution",
        "path_resolution",
        OSError,
    ),
    "PolicyError": ("synapse_channel.core.policy_engine", "policy", ValueError),
    "PrivateDirError": ("synapse_channel.core.private_dir", "private_dir", ValueError),
    "ReceiptSigningError": ("synapse_channel.core.receipt_signing", "receipt_signing", ValueError),
    "RelayTransportError": (
        "synapse_channel.core.operator_relay_transport",
        "relay_transport",
        RuntimeError,
    ),
    "RelayWireError": ("synapse_channel.core.operator_relay_wire", "relay_wire", ValueError),
    "RoleGrantError": ("synapse_channel.core.role_grants", "role_grant", ValueError),
    "SandboxManifestError": ("synapse_channel.core.sandbox_policy", "sandbox_manifest", ValueError),
    "SandboxPathError": ("synapse_channel.core.sandbox_paths", "sandbox_path", RuntimeError),
    "SecretFileError": ("synapse_channel.core.secret_files", "secret_file", ValueError),
    "SecureModeError": ("synapse_channel.core.secure", "secure_mode", ValueError),
    "SqlCipherKeyError": (
        "synapse_channel.core.persistence_sqlcipher",
        "sqlcipher_key",
        ValueError,
    ),
    "SqlCipherUnavailableError": (
        "synapse_channel.core.persistence_sqlcipher",
        "sqlcipher_unavailable",
        RuntimeError,
    ),
    "StateSnapshotError": (
        "synapse_channel.claude_claim_state",
        "claude_claim_state",
        RuntimeError,
    ),
    "StreamError": ("synapse_channel.core.streaming", "stream", ValueError),
    "TeamSecureModeError": ("synapse_channel.core.team_secure", "team_secure_mode", ValueError),
    "WorkflowError": ("synapse_channel.core.workflow", "workflow", ValueError),
}


def _load(name: str) -> type[SynapseError]:
    module_name, _, _ = FROZEN_ERROR_CODES[name]
    module = __import__(module_name, fromlist=[name])
    loaded = getattr(module, name)
    assert isinstance(loaded, type) and issubclass(loaded, SynapseError)
    return loaded


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------


def test_base_code_is_synapse() -> None:
    assert SynapseError.code == "synapse"


def test_subclass_must_declare_its_own_code() -> None:
    with pytest.raises(TypeError, match="must declare its own class-level 'code'"):

        class _Inherited(SynapseError):
            """Missing an explicit code."""


def test_subclass_code_must_be_snake_case() -> None:
    with pytest.raises(TypeError, match="not snake_case"):

        class _Bad(SynapseError):
            """Uppercase code is refused."""

            code = "NotSnake"


def test_deep_subclass_must_also_declare_a_code() -> None:
    class _Parent(SynapseError):
        """A well-formed intermediate."""

        code = "parent_ok"

    with pytest.raises(TypeError, match="must declare its own class-level 'code'"):

        class _Child(_Parent):
            """Inherits the parent code, which is refused."""


def test_error_code_reads_the_taxonomy() -> None:
    class _Local(SynapseError):
        """A local taxonomy member."""

        code = "local_member"

    assert error_code(_Local("boom")) == "local_member"


def test_error_code_is_empty_for_foreign_exceptions() -> None:
    assert error_code(ValueError("plain")) == ""
    assert error_code(KeyboardInterrupt()) == ""


# ---------------------------------------------------------------------------
# Frozen registry
# ---------------------------------------------------------------------------


def test_registry_codes_are_unique() -> None:
    codes = [code for _, code, _ in FROZEN_ERROR_CODES.values()]
    assert len(codes) == len(set(codes)), "two error classes share a code"


@pytest.mark.parametrize("name", sorted(FROZEN_ERROR_CODES))
def test_frozen_class_matches_registry(name: str) -> None:
    cls = _load(name)
    _, code, legacy_base = FROZEN_ERROR_CODES[name]
    assert issubclass(cls, SynapseError)
    assert cls.code == code
    assert "code" in cls.__dict__, f"{name} inherits its code instead of declaring it"
    assert issubclass(cls, legacy_base), (
        f"{name} lost its historical {legacy_base.__name__} base; pre-existing "
        "except clauses would stop catching it"
    )
    instance = cls("probe")
    assert error_code(instance) == code
    assert isinstance(instance, legacy_base)


def test_claim_forward_timeout_still_narrows_to_its_parent() -> None:
    timeout_cls = _load("ClaimForwardTimeoutError")
    parent_cls = _load("ClaimForwardError")
    assert issubclass(timeout_cls, parent_cls)
    assert timeout_cls.code != parent_cls.code


# ---------------------------------------------------------------------------
# Drift gate (AST sweep; no imports, so optional extras cannot mask a miss)
# ---------------------------------------------------------------------------


def _error_classes_in_tree() -> dict[str, tuple[str, list[str]]]:
    """Map every ``*Error`` class defined under ``src/`` to (file, base names)."""
    found: dict[str, tuple[str, list[str]]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                found[node.name] = (str(path.relative_to(_SRC)), bases)
    return found


def test_every_error_class_in_the_tree_joins_the_taxonomy() -> None:
    found = _error_classes_in_tree()
    assert "SynapseError" in found, "core/errors.py must define the base"
    registry_parents = set(FROZEN_ERROR_CODES)
    strays = {
        name: (file, bases)
        for name, (file, bases) in found.items()
        if name != "SynapseError"
        and "SynapseError" not in bases
        and not (set(bases) & registry_parents)
    }
    assert not strays, (
        "error classes outside the taxonomy (derive them from SynapseError and "
        f"add a frozen registry row): {strays}"
    )


def test_registry_covers_every_tree_error_class() -> None:
    found = _error_classes_in_tree()
    tree_names = set(found) - {"SynapseError"}
    missing = tree_names - set(FROZEN_ERROR_CODES)
    stale = set(FROZEN_ERROR_CODES) - tree_names
    assert not missing, f"tree error classes without a frozen registry row: {missing}"
    assert not stale, f"frozen registry rows with no class in the tree: {stale}"
