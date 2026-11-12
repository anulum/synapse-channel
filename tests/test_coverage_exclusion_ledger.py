# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the ledger of every coverage exclusion and conditional test

"""Every coverage exclusion and conditional test is enumerated, or the gate is red.

The repository rule is that no ``pragma: no cover`` and no conditionally
skipped test exists without a tracked justification. This module *is* that
ledger: the tables below name every file that carries an exclusion, its exact
count, and the class of justification. The tests rescan the tree and demand
exact agreement, so adding, moving, or removing an exclusion anywhere forces a
deliberate, reviewable edit here — counts can never drift silently in either
direction.

Justification classes
---------------------
``protocol-body``
    ``typing.Protocol`` member bodies (a docstring and ``...``); Python never
    executes them, so they are structurally unreachable.
``optional-import``
    The raise path when an optional native or hardware dependency is absent
    (TPM, PKCS#11, cloud HSM, cryptography); exercised via a patched import.
``typing-only``
    ``TYPE_CHECKING`` imports that never run at runtime.
``env-defensive``
    Guards for host environments the suite cannot fabricate (``getuser``
    failure, stream-specific refusals).
``validated-helper-boundary``
    Defensive private-helper guards whose public callers validate the same
    structure before dispatch, making the duplicate rejection unreachable
    through the supported API.
``entrypoint``
    ``if __name__ == "__main__"`` dispatch lines.
``blocking-wrapper``
    A process-blocking serve wrapper whose factory is covered by real tests.
``platform-guard``
    Tests for native OS semantics: POSIX-only tests skip Windows, and
    Linux/systemd-user effect tests skip other hosts. Native unsupported-host
    refusal tests run on those other hosts instead.
``platform-native``
    Bodies that call OS-native Win32 (or other non-POSIX) APIs via ctypes and
    can only execute on that OS; pure policy extracted from them is covered on
    every platform, and the native body runs on cross-os Windows CI.
``filesystem-capability``
    Tests for real symlink, hard-link, alternate-case, or Windows short-name
    behavior, skipped only when the host filesystem cannot expose that feature.
``optional-dep-guard``
    Tests that run only where their optional dependency is installed; CI
    installs every one of these, so they run and count toward coverage there.
``operator-smoke``
    Real provider-CLI smokes (claude, codex, kimi, grok, ollama) enabled
    explicitly with ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1``; they spend live
    provider capacity and are an operator decision, never a default.
``collector-header``
    An executed context-manager header excluded only because its pragma
    must also cover the nested collector boundary on the following line.
``collector-boundary``
    The call between two collectors: the outer collector is paused and the
    inner collector starts mid-frame. Real target data and exit tests prove
    the call's effect independently.
``fixed-target-exit``
    A normal return unreachable because both fixed targets always raise
    SystemExit. AST guards pin those target exits and the exact choices.
"""

from __future__ import annotations

import runpy
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every ``pragma: no cover`` in source or the finite probe, with exact counts and classes.
PRAGMA_LEDGER: dict[str, tuple[int, str]] = {
    "src/synapse_channel/a2a_grpc.py": (1, "optional-import"),
    "src/synapse_channel/a2a_http.py": (1, "blocking-wrapper"),
    "src/synapse_channel/a2a_scenario_responses.py": (1, "validated-helper-boundary"),
    "src/synapse_channel/ack.py": (2, "protocol-body"),
    "src/synapse_channel/cli.py": (1, "env-defensive"),
    "src/synapse_channel/cli_doctor_federation.py": (5, "protocol-body"),
    # Lease renewal: register_owner and the exchange absorb connection errors.
    "src/synapse_channel/cli_lock_supervision.py": (1, "validated-helper-boundary"),
    "src/synapse_channel/cli_relay.py": (1, "env-defensive"),
    "src/synapse_channel/commit.py": (2, "protocol-body"),
    "src/synapse_channel/core/aef_verification.py": (
        3,
        "protocol-body + validated-helper-boundary",
    ),
    # Line 45 is the TYPE_CHECKING import: it never runs on either platform.
    # Line 771 is the inner non-regular check. The public check already
    # refused that file, so no real stat reaches the inner branch.
    "src/synapse_channel/core/at_rest.py": (3, "optional-import + typing-only"),
    "src/synapse_channel/core/at_rest_cloud_hsm.py": (1, "optional-import"),
    "src/synapse_channel/core/at_rest_counter.py": (2, "protocol-body"),
    "src/synapse_channel/core/at_rest_pkcs11.py": (1, "optional-import"),
    "src/synapse_channel/core/at_rest_tpm2.py": (1, "optional-import"),
    "src/synapse_channel/core/dead_letter_forwarding.py": (1, "protocol-body"),
    "src/synapse_channel/core/dead_letter_forwarding_transport.py": (2, "protocol-body"),
    "src/synapse_channel/core/federation_fetch.py": (4, "protocol-body"),
    "src/synapse_channel/core/fleet_scorecard_metrics.py": (6, "protocol-body"),
    "src/synapse_channel/core/http_response.py": (1, "protocol-body"),
    "src/synapse_channel/core/mcp_config_launch.py": (3, "optional-import + env-defensive"),
    "src/synapse_channel/core/spend_transport.py": (2, "protocol-body"),
    "src/synapse_channel/core/multihub_equivocation.py": (5, "protocol-body"),
    "src/synapse_channel/core/payload_crypto.py": (1, "optional-import"),
    "src/synapse_channel/core/secure_path.py": (3, "platform-native"),
    "src/synapse_channel/core/tls.py": (2, "protocol-body"),
    "src/synapse_channel/ergonomics.py": (1, "entrypoint"),
    "src/synapse_channel/ergonomics_inbox.py": (2, "protocol-body"),
    "src/synapse_channel/locks.py": (2, "protocol-body"),
    "src/synapse_channel/observed_peers.py": (1, "protocol-body"),
    "src/synapse_channel/reap.py": (3, "protocol-body"),
    "src/synapse_channel/setup_authorization.py": (1, "env-defensive"),
    "src/synapse_channel/setup_executor.py": (8, "env-defensive"),
    "src/synapse_channel/setup_ledger.py": (1, "env-defensive"),
    "src/synapse_channel/setup_verification.py": (4, "env-defensive"),
    "src/synapse_channel/setup_verifier.py": (2, "env-defensive"),
    "tools/protected_runtime_probe/instrumentation_launch.py": (
        2,
        "collector-header + collector-boundary + fixed-target-exit",
    ),
}

#: Exact excluded source lines produced by the probe's two pragma sites.
PROBE_EXCLUSIONS: dict[str, dict[int, tuple[str, str]]] = {
    "tools/protected_runtime_probe/instrumentation_launch.py": {
        175: (
            "collector-header",
            "EXECUTED header excluded only so its pragma covers the collector boundary at 176.",
        ),
        176: (
            "collector-boundary",
            "Outer paused; inner starts mid-frame. Inner data and exit tests prove the call.",
        ),
        179: (
            "fixed-target-exit",
            "Both fixed target main guards unconditionally raise SystemExit; AST tests pin them.",
        ),
    },
}

#: Every conditional-skip line in ``tests/``, by file: (line count, justification class).
SKIP_LEDGER: dict[str, tuple[int, str]] = {
    "tests/_platform_caps.py": (9, "platform-guard"),
    "tests/_portable_exec.py": (1, "platform-guard"),
    # Only Linux SO_PEERCRED journeys use this marker; signed TCP tests stay portable.
    "tests/protected_write_socket_examples.py": (1, "platform-guard"),
    "tests/test_protected_runtime_probe_inventory.py": (1, "platform-guard"),
    "tests/test_protected_runtime_probe_native_closure.py": (11, "platform-guard"),
    "tests/test_protected_runtime_probe_controller.py": (1, "platform-guard"),
    "tests/test_protected_runtime_probe_runtime.py": (1, "platform-guard"),
    "tests/test_protected_write_socket_platform.py": (1, "platform-guard"),
    "tests/fixtures/opencode/process.py": (2, "optional-dep-guard"),
    "tests/test_a2a_grpc.py": (1, "optional-dep-guard"),
    "tests/test_a2a_grpc_security_profiles.py": (1, "optional-dep-guard"),
    "tests/test_a2a_outbound_response.py": (2, "platform-guard"),
    "tests/test_a2a_store.py": (3, "platform-guard"),
    # POSIX directory permissions that bind a non-root owner.
    "tests/test_a2a_store_rollback_and_load_edges.py": (1, "platform-guard"),
    "tests/test_external_surfaces_benchmark.py": (2, "optional-dep-guard"),
    "tests/test_hub_restart.py": (2, "platform-guard"),
    # A whole-hub kill -9 needs POSIX SIGKILL.
    "tests/test_hub_sigkill_durability.py": (1, "platform-guard"),
    "tests/test_path_identity_inode_reuse.py": (3, "platform-guard"),
    "tests/test_token_set.py": (1, "platform-guard"),
    "tests/test_cli_token_set.py": (1, "platform-guard"),
    "tests/test_token_set_hub_status.py": (1, "platform-guard"),
    "tests/test_cli_doctor_token_set.py": (1, "platform-guard"),
    # POSIX mode bits define the group-readable token-set file the CLI must refuse.
    "tests/test_metrics_token_set_cli.py": (1, "platform-guard"),
    # POSIX mode bits define the group-readable key-set file the hub must refuse.
    "tests/test_message_auth_key_set_cli.py": (1, "platform-guard"),
    # The Windows C runtime is the only place a non-contention lock error is raised.
    "tests/test_native_hook_spool.py": (1, "platform-guard"),
    "tests/test_worker_session_waiter_cleanup.py": (1, "platform-guard"),
    # Includes the real Linux executable-format fallback/process-image regression.
    "tests/test_launch_record.py": (8, "platform-guard"),
    "tests/test_read_owner_only_file_bytes.py": (3, "platform-guard"),
    "tests/test_analysis_sqlcipher_readers.py": (1, "optional-dep-guard"),
    # POSIX mode bits, geteuid, and the Windows NT DACL key floor.
    "tests/test_at_rest.py": (4, "platform-guard"),
    "tests/test_at_rest_cloud_hsm.py": (1, "platform-guard"),
    "tests/test_at_rest_pkcs11.py": (1, "optional-dep-guard"),
    "tests/test_at_rest_tpm2.py": (1, "optional-dep-guard"),
    "tests/test_benchmark.py": (1, "optional-dep-guard"),
    # tmux availability plus separate opt-in OpenCode and Codex composer smokes.
    "tests/test_cli_e2e_agent_tmux.py": (3, "optional-dep-guard + operator-smoke"),
    "tests/test_cli_e2e_agent_tmux_pending_wait.py": (1, "optional-dep-guard"),
    "tests/test_cli_e2e_opencode_editors.py": (1, "operator-smoke"),
    "tests/test_cli_e2e_mutation_status.py": (1, "platform-guard"),
    # Native supervisor suspension needs POSIX SIGSTOP/SIGCONT.
    "tests/test_cli_lock_stall.py": (1, "platform-guard"),
    "tests/test_cli_sqlcipher.py": (2, "optional-dep-guard"),
    "tests/test_cli_sqlcipher_refusals.py": (1, "optional-dep-guard"),
    # POSIX directory permissions that bind a non-root owner.
    "tests/test_claude_plugin_install_edges.py": (1, "platform-guard"),
    "tests/test_diagnostics_store_and_mcp_edges.py": (1, "optional-dep-guard"),
    "tests/test_cli_streams_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_codex_tmux.py": (1, "optional-dep-guard"),
    "tests/test_codex_mcp_package.py": (10, "optional-dep-guard"),
    "tests/test_claim_coverage.py": (2, "platform-guard"),
    "tests/test_cli.py": (1, "platform-guard"),
    # Embedded NT-only subprocess fixture verifies that real platform skips survive sharding.
    "tests/test_ci_portable_shards.py": (1, "platform-guard"),
    "tests/test_cli_completions.py": (2, "platform-guard"),
    "tests/test_cli_claude_plugin.py": (8, "optional-dep-guard"),
    "tests/test_cli_doctor.py": (1, "platform-guard"),
    "tests/test_cli_encrypt_key.py": (1, "platform-guard"),
    "tests/test_cli_git.py": (1, "platform-guard"),
    "tests/test_cli_pid_monitor.py": (1, "optional-dep-guard"),
    "tests/test_dashboard_access_store.py": (1, "platform-guard"),
    "tests/test_dashboard_feeds_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_event_row_mac.py": (1, "optional-dep-guard"),
    "tests/test_dashboard_host_sessions.py": (1, "optional-dep-guard"),
    "tests/test_delivery_participant_bridge.py": (2, "optional-dep-guard"),
    "tests/test_host_sessions.py": (1, "optional-dep-guard"),
    "tests/test_host_sessions_proc.py": (1, "optional-dep-guard"),
    "tests/test_host_sessions_tmux.py": (1, "optional-dep-guard"),
    "tests/test_hub_sqlcipher_e2e.py": (1, "optional-dep-guard"),
    "tests/test_identity_keys_secret_floor.py": (1, "platform-guard"),
    "tests/test_integration_catalog.py": (3, "optional-dep-guard"),
    "tests/test_multihub_mcp_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_opencode_compatibility_smoke.py": (2, "operator-smoke"),
    # Linux procfs and tmux; the foreign-group case needs a process group the
    # test user may not signal, which a single-user container may not have.
    "tests/test_opencode_composer_retire.py": (2, "platform-guard"),
    "tests/test_operator_sqlcipher_readers.py": (1, "optional-dep-guard"),
    "tests/test_ordinary_claim_scope.py": (1, "filesystem-capability"),
    "tests/test_participant_api_ollama_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_codex_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_grok_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_headless_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_kimi_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_mixed_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_ollama_smoke.py": (1, "operator-smoke"),
    "tests/test_path_identity.py": (8, "filesystem-capability"),
    "tests/test_path_identity_fail_closed.py": (1, "filesystem-capability"),
    "tests/test_persistence.py": (2, "platform-guard"),
    "tests/test_persistence_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_private_dir.py": (1, "platform-guard"),
    "tests/test_protected_write_descriptors.py": (1, "platform-guard"),
    "tests/test_protected_write_driver.py": (1, "platform-guard"),
    "tests/test_pane_wake.py": (1, "optional-dep-guard"),
    "tests/test_protected_write_execution_journal.py": (1, "platform-guard"),
    "tests/test_protected_write_execution_observations.py": (1, "platform-guard"),
    "tests/test_protected_write_execution_steps.py": (1, "platform-guard"),
    "tests/test_protected_write_inspection.py": (1, "platform-guard"),
    "tests/test_protected_write_preparation.py": (1, "platform-guard"),
    "tests/test_protected_write_storage_layout.py": (1, "platform-guard"),
    "tests/test_relay_trim.py": (3, "platform-guard"),
    "tests/test_reliability_workflow_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_secret_files.py": (8, "platform-guard"),
    "tests/test_secure_path.py": (6, "platform-guard"),
    # The stand-in git that keeps its pipes open is a POSIX shell script.
    "tests/test_semantic_diff.py": (1, "platform-guard"),
    "tests/test_service_status.py": (1, "platform-guard"),
    "tests/test_service_templates.py": (2, "platform-guard"),
    "tests/test_session_capability_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_setup_executor.py": (2, "platform-guard"),
    "tests/test_setup_verifier.py": (6, "platform-guard"),
    "tests/test_shared_host_regression.py": (1, "platform-guard"),
    "tests/test_shell_integration.py": (1, "optional-dep-guard"),
    # The positive liveness control reads Linux /proc.
    "tests/test_shell_integration_pid_bounds.py": (1, "platform-guard"),
    "tests/test_terminal_label_tmux.py": (1, "platform-guard"),
    "tests/test_wake_adapter_codex_queue.py": (1, "platform-guard"),
    "tests/test_wake_adapter_codex_queue_windows.py": (1, "platform-guard"),
    "tests/test_wake_adapter_pane_bridge.py": (1, "platform-guard"),
    # Pane sessions are read from Linux procfs and need tmux.
    "tests/test_tmux_reap.py": (1, "platform-guard"),
    "tests/test_tmux_server.py": (2, "platform-guard"),
    "tests/test_worker_session.py": (2, "platform-guard"),
    # A descriptor that outlives a rename over its path exists only on POSIX.
    "tests/test_worker_outcome_publication.py": (1, "platform-guard"),
    # uid injection and fchmod failure exist only where those POSIX calls exist.
    "tests/test_kimi_hook_config_file.py": (2, "platform-guard"),
}

# Assembled from fragments so this ledger's own source never matches its scan.
_PRAGMA_TOKEN = "# pragma: " + "no cover"
_SKIP_TOKENS = (
    "pytest.mark." + "skip",
    "pytest." + "skip(",
    "x" + "fail",
    "@requires_packaged_" + "python312",
    "marks=requires_packaged_" + "python312",
)
_SKIP_EXEMPT = "importor" + "skip"


def _count_matches(
    root: Path, tokens: tuple[str, ...], *, exempt: str | None = None
) -> dict[str, int]:
    """Count lines under ``root`` that contain any of ``tokens``, per file.

    Parameters
    ----------
    root : Path
        Directory whose ``*.py`` files (recursively) are scanned.
    tokens : tuple of str
        Substrings; a line counts when it contains any of them.
    exempt : str, optional
        A substring that exempts a line even when a token matches.

    Returns
    -------
    dict of str to int
        Repo-relative posix path -> number of matching lines, for files with
        at least one match.
    """
    counts: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        matches = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if exempt is not None and exempt in line:
                continue
            if any(token in line for token in tokens):
                matches += 1
        if matches:
            counts[path.relative_to(REPO_ROOT).as_posix()] = matches
    return counts


def _diff(observed: dict[str, int], ledger: dict[str, tuple[int, str]]) -> list[str]:
    """Describe every disagreement between a scan and its ledger.

    Parameters
    ----------
    observed : dict of str to int
        Per-file counts found in the tree.
    ledger : dict of str to (int, str)
        Per-file expected counts with their justification class.

    Returns
    -------
    list of str
        One human-readable line per disagreement; empty when in sync.
    """
    problems: list[str] = []
    for path, count in sorted(observed.items()):
        if path not in ledger:
            problems.append(f"{path}: {count} unledgered — justify it here or remove it")
        elif ledger[path][0] != count:
            problems.append(f"{path}: ledger says {ledger[path][0]}, tree has {count}")
    for path in sorted(set(ledger) - set(observed)):
        problems.append(f"{path}: ledgered but clean in the tree — drop its stale row")
    return problems


def test_every_pragma_no_cover_is_ledgered_with_a_justification() -> None:
    """Each source or finite-probe coverage pragma matches this ledger exactly."""
    observed = _count_matches(REPO_ROOT / "src", (_PRAGMA_TOKEN,))
    observed.update(_count_matches(REPO_ROOT / "tools/protected_runtime_probe", (_PRAGMA_TOKEN,)))
    problems = _diff(observed, PRAGMA_LEDGER)
    assert not problems, "coverage-exclusion ledger out of sync:\n" + "\n".join(problems)


def test_probe_pragma_sites_exclude_only_the_exact_justified_lines() -> None:
    """A memory-only public analysis must match all three explicitly justified lines."""
    import coverage

    policy = coverage.Coverage(data_file=None, config_file=False)
    try:
        for name, entries in PROBE_EXCLUSIONS.items():
            assert policy.analysis2(str(REPO_ROOT / name))[2] == sorted(entries)
            assert all(reason_class and reason for reason_class, reason in entries.values())
    finally:
        policy.get_data().close(force=True)


def test_every_conditional_skip_is_ledgered_with_a_justification() -> None:
    """Each skip guard in ``tests/`` matches this ledger exactly."""
    observed = _count_matches(REPO_ROOT / "tests", _SKIP_TOKENS, exempt=_SKIP_EXEMPT)
    problems = _diff(observed, SKIP_LEDGER)
    assert not problems, "conditional-skip ledger out of sync:\n" + "\n".join(problems)


def test_the_ledgers_carry_no_unconditional_skips() -> None:
    """The suite tolerates guards, never unconditional exclusions of tests.

    Every ledgered class is conditional (platform, optional dependency,
    operator opt-in). An unconditional mark would slip through the count
    gate, so the classes themselves are pinned to the known-conditional set.
    """
    allowed = {
        "filesystem-capability",
        "operator-smoke",
        "optional-dep-guard",
        "platform-guard",
    }
    observed = {
        reason_class for _, reason in SKIP_LEDGER.values() for reason_class in reason.split(" + ")
    }
    assert observed <= allowed


def test_public_skip_gate_refuses_actual_untracked_drifted_and_absent_sources(
    tmp_path: Path,
) -> None:
    """Run the public ledger gate against real source drift in a disposable repository.

    The scanner must refuse all three inventory failures introduced together:
    an untracked guard, a changed guard count and an absent ledgered source.
    The actual checker bytes are copied unchanged; production sources stay intact.
    """
    repository = tmp_path / "ledger-repository"
    shutil.copytree(
        REPO_ROOT / "tests",
        repository / "tests",
        ignore=shutil.ignore_patterns("__pycache__", "protected_write_socket_examples.py"),
    )
    checker = repository / "checkers" / "ledger.py"
    checker.parent.mkdir()
    shutil.copyfile(Path(__file__), checker)
    assert checker.read_bytes() == Path(__file__).read_bytes()
    guard = "pytest.mark." + "skipif(False, reason='native capability fixture')\n"
    (repository / "tests" / "untracked_guard.py").write_text(guard, encoding="utf-8")
    drifted = repository / "tests" / "_portable_exec.py"
    with drifted.open("a", encoding="utf-8") as stream:
        stream.write("\n" + guard)
    namespace = runpy.run_path(str(checker))
    with pytest.raises(AssertionError) as refused:
        namespace["test_every_conditional_skip_is_ledgered_with_a_justification"]()
    message = str(refused.value)
    assert "tests/untracked_guard.py: 1 unledgered" in message
    assert "tests/_portable_exec.py: ledger says 1, tree has 2" in message
    assert "tests/protected_write_socket_examples.py: ledgered but clean in the tree" in message


def test_the_ledger_diff_names_every_kind_of_disagreement() -> None:
    """An unledgered file, a changed count and a stale row are each reported."""
    problems = _diff(
        {"new.py": 1, "changed.py": 2},
        {"changed.py": (3, "platform-guard"), "stale.py": (1, "platform-guard")},
    )
    assert problems == [
        "changed.py: ledger says 3, tree has 2",
        "new.py: 1 unledgered — justify it here or remove it",
        "stale.py: ledgered but clean in the tree — drop its stale row",
    ]
