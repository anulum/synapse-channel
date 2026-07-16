# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Emacs Agent Shell ACP lifecycle contract tests
"""Pin the real Emacs client teardown to behavioural ACP transport evidence."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CLIENT = _ROOT / "tests" / "e2e" / "opencode_editors" / "emacs_client.el"
_ERT = _ROOT / "tests" / "e2e" / "opencode_editors" / "emacs_client_test.el"
_WORKFLOW = _ROOT / ".github" / "workflows" / "opencode-editor-e2e.yml"


def test_emacs_workflow_executes_transport_ert_before_the_real_turn() -> None:
    """Require the pinned Emacs lane to execute the behavioural ERT contract."""
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    ert_step = "Exercise Emacs transport lifecycle branches"
    real_turn = "Real editor client to OpenCode ACP turn"

    assert workflow.index(ert_step) < workflow.index(real_turn)
    assert '"$SYNAPSE_EMACS_BIN" --batch -Q \\' in workflow
    assert "-l tests/e2e/opencode_editors/emacs_client_test.el \\" in workflow
    assert "-f ert-run-tests-batch-and-exit" in workflow


def test_emacs_client_loads_transport_before_waiting_and_teardown() -> None:
    """Keep the production client wired to transport draining before teardown."""
    source = _CLIENT.read_text(encoding="utf-8")
    load = '(expand-file-name "emacs_transport.el"'
    wait = "(synapse-e2e-wait-for-transport-quiescence buffer 10)"
    teardown = "(kill-buffer buffer)"

    assert source.index(load) < source.index(wait) < source.index(teardown)


def test_emacs_ert_covers_trackers_resampling_and_timeout() -> None:
    """Guard the behavioural branch set executed by the pinned Emacs lane."""
    source = _ERT.read_text(encoding="utf-8")

    assert "synapse-e2e-transport-requires-both-trackers-idle" in source
    assert "synapse-e2e-transport-resets-the-stability-window" in source
    assert "synapse-e2e-transport-resamples-after-output-service" in source
    assert "synapse-e2e-transport-times-out-while-active" in source
    assert "(sleep-for" not in source
