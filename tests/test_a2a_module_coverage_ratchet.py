# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A module coverage ratchet

from __future__ import annotations

from tools.check_a2a_module_coverage import evaluate_report


def test_a2a_module_coverage_ratchet_reports_each_weak_module() -> None:
    report = {
        "files": {
            "src/synapse_channel/a2a_server.py": {"summary": {"percent_covered": 99.99}},
            "src/synapse_channel/cli_a2a.py": {"summary": {"percent_covered": 100.0}},
            "src/synapse_channel/a2a_events.py": {"summary": {"percent_covered": 100.0}},
            "src/synapse_channel/a2a_store.py": {"summary": {"percent_covered": 97.5}},
        }
    }

    failures = evaluate_report(report)

    assert failures == [
        "src/synapse_channel/a2a_server.py: 99.99% < required 100.00%",
        "src/synapse_channel/a2a_store.py: 97.50% < required 100.00%",
    ]


def test_a2a_module_coverage_ratchet_accepts_exact_thresholds() -> None:
    report = {
        "files": {
            "src/synapse_channel/a2a_server.py": {"summary": {"percent_covered": 100.0}},
            "src/synapse_channel/cli_a2a.py": {"summary": {"percent_covered": 100.0}},
            "src/synapse_channel/a2a_events.py": {"summary": {"percent_covered": 100.0}},
            "src/synapse_channel/a2a_store.py": {"summary": {"percent_covered": 100.0}},
        }
    }

    assert evaluate_report(report) == []
