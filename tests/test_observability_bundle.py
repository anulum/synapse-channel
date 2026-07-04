# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — drift guard between the observability bundle and the registry

"""The committed Grafana/Prometheus bundle must never drift from the registry.

Every metric name a dashboard panel or an alert rule references must exist in
:func:`collect_hub_metrics` — otherwise an operator imports the bundle and gets
silent empty panels or rules that can never fire, which reads as "everything
is fine" when nothing is measured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from synapse_channel.core.causality_health import run_causal_health
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.metrics import collect_hub_metrics
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.reliability import run_reliability_report
from synapse_channel.observability_textfile import (
    render_health_textfile,
    render_reliability_textfile,
)

BUNDLE = Path(__file__).resolve().parent.parent / "integrations" / "observability"


def _textfile_metric_names(tmp: Path) -> set[str]:
    """The metric family names the log-derived textfiles emit, for the drift guard."""
    db = tmp / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "a", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()
    text = render_reliability_textfile(run_reliability_report(db)) + render_health_textfile(
        run_causal_health(db)
    )
    return _referenced_names(text)


# The live hub registry plus the log-derived textfile families: an alert rule may
# reference either plane, and both are pinned so a renamed metric fails the suite.
EXPORTED = {metric.name for metric in collect_hub_metrics(SynapseHub())}

_METRIC_NAME = re.compile(r"\bsynapse_[a-z_]+\b")


def _referenced_names(text: str) -> set[str]:
    return set(_METRIC_NAME.findall(text))


def test_dashboard_references_only_exported_metrics() -> None:
    document = json.loads((BUNDLE / "grafana-dashboard-synapse-hub.json").read_text("utf-8"))
    exprs = [target["expr"] for panel in document["panels"] for target in panel.get("targets", [])]
    assert exprs, "dashboard has no queries"
    referenced = _referenced_names("\n".join(exprs))
    assert referenced, "no synapse metrics referenced"
    assert referenced <= EXPORTED, f"dashboard references unknown metrics: {referenced - EXPORTED}"


def test_dashboard_panels_are_wired_to_the_datasource_input() -> None:
    document = json.loads((BUNDLE / "grafana-dashboard-synapse-hub.json").read_text("utf-8"))
    assert document["uid"] == "synapse-hub"
    assert any(inp["name"] == "DS_PROMETHEUS" for inp in document["__inputs"])
    for panel in document["panels"]:
        assert panel["datasource"]["uid"] == "${DS_PROMETHEUS}", panel["title"]


def test_alert_rules_reference_only_exported_metrics(tmp_path: Path) -> None:
    document = yaml.safe_load((BUNDLE / "prometheus-alerts.yml").read_text("utf-8"))
    rules = [rule for group in document["groups"] for rule in group["rules"]]
    assert len(rules) >= 8
    known = EXPORTED | _textfile_metric_names(tmp_path)
    for rule in rules:
        referenced = _referenced_names(str(rule["expr"]))
        assert referenced <= known, (
            f"alert {rule['alert']} references unknown metrics: {referenced - EXPORTED}"
        )
        assert rule["annotations"]["summary"]
        assert rule["annotations"]["description"]
        assert rule["labels"]["severity"] in {"critical", "warning"}


def test_persistent_dead_letter_alert_watches_the_targets_gauge() -> None:
    """The persistent-blackhole alert reads the aged-out targets gauge, sustained.

    Distinct from ``SynapseDeadLettersGrowing`` (which fires on ledger growth),
    this fires when a name stays blackholed past a window — meaningful only
    because the ledger now ages quiet names out, so a still-counted target is a
    genuine persistent gap. Pins the gauge, the sustain window, and that the
    gauge is a real exported series.
    """
    document = yaml.safe_load((BUNDLE / "prometheus-alerts.yml").read_text("utf-8"))
    rules = {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}
    alert = rules["SynapsePersistentDeadLetters"]
    assert "synapse_dead_letter_targets" in alert["expr"]
    assert "synapse_dead_letter_targets" in EXPORTED  # the gauge is really exported
    assert alert["for"] == "1h"  # sustained, not a passing miss
    assert alert["labels"]["severity"] == "warning"


def test_scrape_job_targets_the_metrics_path() -> None:
    document = yaml.safe_load((BUNDLE / "prometheus-scrape.yml").read_text("utf-8"))
    jobs = document["scrape_configs"]
    assert jobs[0]["job_name"] == "synapse-hub"
    assert jobs[0]["metrics_path"] == "/metrics"


def test_docs_page_names_every_operational_counter_family() -> None:
    page = (Path(__file__).resolve().parent.parent / "docs" / "observability.md").read_text("utf-8")
    for name in (
        "synapse_claims_denied_total",
        "synapse_dead_letters",
        "synapse_auth_failures_total",
        "synapse_federation_denied_total",
        "synapse_takeover_quarantines_total",
    ):
        assert name in page, name
