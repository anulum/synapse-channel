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
``entrypoint``
    ``if __name__ == "__main__"`` dispatch lines.
``blocking-wrapper``
    A process-blocking serve wrapper whose factory is covered by real tests.
``platform-guard``
    Tests for POSIX-only semantics, skipped on Windows runners.
``optional-dep-guard``
    Tests that run only where their optional dependency is installed; CI
    installs every one of these, so they run and count toward coverage there.
``operator-smoke``
    Real provider-CLI smokes (claude, codex, kimi, grok, ollama) enabled
    explicitly with ``SYNAPSE_PARTICIPANT_REAL_SMOKE=1``; they spend live
    provider capacity and are an operator decision, never a default.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every ``pragma: no cover`` in ``src/``, by file: (line count, justification class).
PRAGMA_LEDGER: dict[str, tuple[int, str]] = {
    "src/synapse_channel/a2a_http.py": (1, "blocking-wrapper"),
    "src/synapse_channel/ack.py": (2, "protocol-body"),
    "src/synapse_channel/cli.py": (1, "env-defensive"),
    "src/synapse_channel/cli_doctor_federation.py": (5, "protocol-body"),
    "src/synapse_channel/cli_relay.py": (1, "env-defensive"),
    "src/synapse_channel/commit.py": (2, "protocol-body"),
    "src/synapse_channel/core/at_rest.py": (3, "optional-import + typing-only"),
    "src/synapse_channel/core/at_rest_cloud_hsm.py": (1, "optional-import"),
    "src/synapse_channel/core/at_rest_counter.py": (2, "protocol-body"),
    "src/synapse_channel/core/at_rest_pkcs11.py": (1, "optional-import"),
    "src/synapse_channel/core/at_rest_tpm2.py": (1, "optional-import"),
    "src/synapse_channel/core/dead_letter_forwarding.py": (1, "protocol-body"),
    "src/synapse_channel/core/dead_letter_forwarding_transport.py": (2, "protocol-body"),
    "src/synapse_channel/core/federation_fetch.py": (4, "protocol-body"),
    "src/synapse_channel/core/fleet_scorecard_metrics.py": (6, "protocol-body"),
    "src/synapse_channel/core/multihub_claim_transport.py": (4, "protocol-body"),
    "src/synapse_channel/core/multihub_transport.py": (4, "protocol-body"),
    "src/synapse_channel/core/operator_relay_transport.py": (4, "protocol-body"),
    "src/synapse_channel/core/payload_crypto.py": (1, "optional-import"),
    "src/synapse_channel/core/tls.py": (2, "protocol-body"),
    "src/synapse_channel/ergonomics.py": (1, "entrypoint"),
    "src/synapse_channel/locks.py": (2, "protocol-body"),
    "src/synapse_channel/observed_peers.py": (1, "protocol-body"),
    "src/synapse_channel/reap.py": (3, "protocol-body"),
}

#: Every conditional-skip line in ``tests/``, by file: (line count, justification class).
SKIP_LEDGER: dict[str, tuple[int, str]] = {
    "tests/test_a2a_store.py": (3, "platform-guard"),
    "tests/test_analysis_sqlcipher_readers.py": (1, "optional-dep-guard"),
    "tests/test_at_rest_pkcs11.py": (1, "optional-dep-guard"),
    "tests/test_at_rest_tpm2.py": (1, "optional-dep-guard"),
    "tests/test_benchmark.py": (1, "optional-dep-guard"),
    "tests/test_cli_e2e_agent_tmux.py": (1, "optional-dep-guard"),
    "tests/test_cli_sqlcipher.py": (2, "optional-dep-guard"),
    "tests/test_cli_streams_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_dashboard_feeds_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_hub_sqlcipher_e2e.py": (1, "optional-dep-guard"),
    "tests/test_multihub_mcp_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_operator_sqlcipher_readers.py": (1, "optional-dep-guard"),
    "tests/test_packaging_extras.py": (1, "optional-dep-guard"),
    "tests/test_participant_api_ollama_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_codex_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_grok_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_headless_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_kimi_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_mixed_smoke.py": (1, "operator-smoke"),
    "tests/test_participant_ollama_smoke.py": (1, "operator-smoke"),
    "tests/test_persistence.py": (2, "platform-guard"),
    "tests/test_persistence_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_relay_trim.py": (2, "platform-guard"),
    "tests/test_reliability_workflow_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_session_capability_sqlcipher.py": (1, "optional-dep-guard"),
    "tests/test_shell_integration.py": (1, "optional-dep-guard"),
    "tests/test_worker_session.py": (1, "platform-guard"),
}

# Assembled from fragments so this ledger's own source never matches its scan.
_PRAGMA_TOKEN = "# pragma: " + "no cover"
_SKIP_TOKENS = ("pytest.mark." + "skip", "pytest." + "skip(", "x" + "fail")
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
    """Each coverage pragma in ``src/`` matches this ledger exactly."""
    observed = _count_matches(REPO_ROOT / "src", (_PRAGMA_TOKEN,))
    problems = _diff(observed, PRAGMA_LEDGER)
    assert not problems, "coverage-exclusion ledger out of sync:\n" + "\n".join(problems)


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
    allowed = {"platform-guard", "optional-dep-guard", "operator-smoke"}
    assert {reason for _, reason in SKIP_LEDGER.values()} <= allowed
