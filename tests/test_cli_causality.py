# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coordination-causality CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli, cli_causality
from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed(path: Path) -> None:
    """B done & released; A depends on B and is claimed after; C contends A's paths."""
    store = EventStore(path)
    store.append(EventKind.LEDGER_TASK, {"task_id": "B", "title": "B", "depends_on": []}, ts=1.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "B",
            "owner": "alice",
            "status": "claimed",
            "paths": ["src/x"],
            "worktree": "w",
        },
        ts=2.0,
    )
    store.append(
        EventKind.TASK_UPDATE,
        {"task_id": "B", "owner": "alice", "status": "done", "paths": ["src/x"], "worktree": "w"},
        ts=3.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=4.0)
    store.append(EventKind.LEDGER_TASK, {"task_id": "A", "title": "A", "depends_on": ["B"]}, ts=5.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "bob", "status": "claimed", "paths": ["src/y"], "worktree": "w"},
        ts=6.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "A"}, ts=7.0)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "C",
            "owner": "carol",
            "status": "claimed",
            "paths": ["src/y"],
            "worktree": "w",
        },
        ts=8.0,
    )
    store.close()


def test_parser_wires_causality_command() -> None:
    args = cli.build_parser().parse_args(["causality", "effects", "hub.db", "6", "--json"])

    assert args.command == "causality"
    assert args.direction == "effects"
    assert args.db == "hub.db"
    assert args.seq == "6"
    assert args.json is True
    assert args.peer == []
    assert args.hub_id is None


def test_parser_accepts_repeated_peers_and_a_primary_hub_id() -> None:
    args = cli.build_parser().parse_args(
        [
            "causality",
            "causes",
            "hub.db",
            "peer-a:6",
            "--peer",
            "peer-a=a.db",
            "--peer",
            "peer-b=b.db",
            "--hub-id",
            "primary",
        ]
    )

    assert args.peer == ["peer-a=a.db", "peer-b=b.db"]
    assert args.hub_id == "primary"
    assert args.seq == "peer-a:6"


def test_cli_causality_causes_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "6"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Causality (causes): seq 6" in out
    assert "[dependency]" in out


def test_cli_causality_effects_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "effects", str(db), "4", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["direction"] == "effects"
    assert payload["present"] is True
    assert [node["seq"] for node in payload["transitive"]] == [6, 7, 8]


def test_cli_causality_counterfactual_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "counterfactual", str(db), "2"])

    assert exit_code == 0
    assert "Loses recorded support" in capsys.readouterr().out


def test_cli_causality_absent_sequence_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "999"])

    assert exit_code == 1
    assert "No coordination event at seq 999" in capsys.readouterr().out


def test_cli_causality_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "causes", str(tmp_path / "absent.db"), "1"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_causality_rejects_unknown_direction(tmp_path: Path) -> None:
    # argparse choices guard the direction before the handler runs.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["causality", "sideways", "hub.db", "1"])


def test_docs_wire_causality_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse causality" in combined
    assert "counterfactual" in combined


def test_parser_defaults_the_node_ceiling() -> None:
    args = cli.build_parser().parse_args(["causality", "effects", "hub.db", "6"])
    assert args.max_nodes == DEFAULT_MAX_GRAPH_NODES


def test_cli_causality_over_the_node_ceiling_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An over-ceiling log errors with the compact remedy instead of loading."""
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "effects", str(db), "6", "--max-nodes", "1"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "would exceed 1 coordination events" in err
    assert "synapse compact" in err


def test_cli_causality_zero_lifts_the_node_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    assert cli.main(["causality", "effects", str(db), "6", "--max-nodes", "0"]) == 0
    assert "# Causality (effects): seq 6" in capsys.readouterr().out


# --- contention mode ----------------------------------------------------------------


def _seed_contention(path: Path) -> None:
    """Two live claims by different owners overlap on src/y in one worktree."""
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        {"task_id": "A", "owner": "bob", "status": "claimed", "paths": ["src/y"], "worktree": "w"},
        ts=1.0,
    )
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "C",
            "owner": "carol",
            "status": "claimed",
            "paths": ["src/y"],
            "worktree": "w",
        },
        ts=2.0,
    )
    store.close()


def test_cli_contention_reports_the_yielder_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "# Contention: 1 overlapping live claim pair(s)" in out
    assert "## C (carol) should yield to A (bob)" in out
    assert "advisory only: no claim is preempted" in out


def test_cli_contention_quiet_log_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)  # C is the only live claim; nothing overlaps

    exit_code = cli.main(["causality", "contention", str(db)])

    assert exit_code == 0
    assert "No live claims overlap" in capsys.readouterr().out


def test_cli_contention_json_carries_both_standings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload[0]["holder"]["task_id"] == "A"
    assert payload[0]["yielder"]["owner"] == "carol"
    assert "later claim" in payload[0]["reason"]


def test_cli_contention_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "contention", str(tmp_path / "absent.db")])
    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_contention_honours_the_node_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_contention(db)

    exit_code = cli.main(["causality", "contention", str(db), "--max-nodes", "1"])

    assert exit_code == 2
    assert "would exceed 1 coordination events" in capsys.readouterr().err


def test_cli_sequence_directions_still_require_a_seq(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db)])

    assert exit_code == 2
    assert "requires an event SEQ" in capsys.readouterr().err


def _seed_peer(path: Path) -> None:
    """P depends on B (completed on the primary hub) and is claimed here."""
    store = EventStore(path)
    store.append(EventKind.LEDGER_TASK, {"task_id": "P", "title": "P", "depends_on": ["B"]}, ts=5.0)
    store.append(
        EventKind.CLAIM,
        {"task_id": "P", "owner": "pete", "status": "claimed", "paths": ["src/z"], "worktree": "w"},
        ts=6.0,
    )
    store.close()


def _federated_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Seed the primary hub (stem ``hub``) and a peer whose claim depends on it."""
    db = tmp_path / "hub.db"
    peer = tmp_path / "peer.db"
    _seed(db)
    _seed_peer(peer)
    return db, peer


def test_cli_federated_causes_cross_the_hub_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Federated causality (causes): peer:2" in out
    assert "- Hubs: hub, peer" in out
    assert "[federation:dependency] hub:4" in out


def test_cli_federated_json_carries_relation_and_basis(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hubs"] == ["hub", "peer"]
    federated = [link for link in payload["direct"] if link["relation"] == "federation"]
    assert federated
    assert federated[0]["basis"] == "dependency"
    assert federated[0]["src"] == {"hub_id": "hub", "seq": 4}


def test_cli_federated_dot_renders_clusters_and_coloured_federation_edges(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--dot"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("digraph federated_causality {")
    assert 'label="hub";' in out
    assert 'label="peer";' in out
    assert 'label="federation:dependency", color=blue];' in out


def test_cli_dot_requires_a_federated_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--dot"])

    assert exit_code == 2
    assert "it requires --peer" in capsys.readouterr().err


def test_cli_json_and_dot_are_mutually_exclusive(tmp_path: Path) -> None:
    db, peer = _federated_pair(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["causality", "causes", str(db), "peer:2", "--peer", f"peer={peer}", "--json", "--dot"]
        )
    assert excinfo.value.code == 2


def test_cli_federated_plain_seq_resolves_to_the_primary_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "effects", str(db), "4", "--peer", f"peer={peer}"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Federated causality (effects): hub:4" in out
    assert "peer:2" in out


def test_cli_federated_hub_id_overrides_the_db_stem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        [
            "causality",
            "causes",
            str(db),
            "main:4",
            "--peer",
            f"peer={peer}",
            "--hub-id",
            "main",
        ]
    )

    assert exit_code == 0
    assert "# Federated causality (causes): main:4" in capsys.readouterr().out


def test_cli_hub_id_without_peer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--hub-id", "main"])

    assert exit_code == 2
    assert "requires --peer" in capsys.readouterr().err


def test_cli_contention_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "contention", str(db), "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err


def test_cli_federated_malformed_peer_spec_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--peer", "no-equals-here"])

    assert exit_code == 2
    assert "expected HUB=PATH" in capsys.readouterr().err


def test_cli_federated_duplicate_hub_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--peer", f"hub={peer}"])

    assert exit_code == 2
    assert "duplicate hub id 'hub'" in capsys.readouterr().err


def test_cli_single_hub_non_integer_seq_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "abc"])

    assert exit_code == 2
    assert "invalid SEQ 'abc'" in capsys.readouterr().err


def test_cli_federated_malformed_reference_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:abc", "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "expected SEQ or HUB:SEQ" in capsys.readouterr().err


def test_cli_federated_absent_reference_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "causes", str(db), "peer:999", "--peer", f"peer={peer}"])

    assert exit_code == 1
    assert "No coordination event at peer:999" in capsys.readouterr().out


def test_cli_federated_missing_peer_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "causes", str(db), "4", "--peer", f"peer={tmp_path / 'absent.db'}"]
    )

    assert exit_code == 2
    assert "missing event store for hub 'peer'" in capsys.readouterr().err


def test_parser_defaults_otel_flags_to_none() -> None:
    args = cli.build_parser().parse_args(["causality", "otel", "hub.db"])

    assert args.direction == "otel"
    assert args.out is None
    assert args.endpoint is None
    assert args.service_name is None
    assert args.filter == []
    assert args.watch is False
    assert args.interval == 2.0
    assert args.count == 0


def test_cli_otel_writes_span_records_to_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(out)])

    assert exit_code == 0
    assert "exported 11 span(s) across 3 trace(s)" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 3
    linked = [span for span in payload["spans"] if span["links"]]
    assert linked


def test_cli_otel_requires_exactly_one_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    neither = cli.main(["causality", "otel", str(db)])
    assert neither == 2
    assert "exactly one of --out FILE or --endpoint URL" in capsys.readouterr().err

    both = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "s.json"),
            "--endpoint",
            "http://c:4318/v1/traces",
        ]
    )
    assert both == 2


def test_cli_otel_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--peer", f"peer={peer}"]
    )

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err


def test_cli_otel_flags_are_refused_outside_otel_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--out", str(tmp_path / "s.json")])

    assert exit_code == 2
    assert "belong to the otel mode" in capsys.readouterr().err

    for flag in (["--service-name", "hub-eu"], ["--filter", "B"]):
        exit_code = cli.main(["causality", "causes", str(db), "4", *flag])
        assert exit_code == 2
        assert "belong to the otel mode" in capsys.readouterr().err

    exit_code = cli.main(["causality", "causes", str(db), "4", "--watch"])
    assert exit_code == 2
    assert "--watch re-runs the otel or health mode" in capsys.readouterr().err


def test_cli_otel_service_name_flows_into_the_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(out), "--service-name", "hub-eu"]
    )

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["service_name"] == "hub-eu"
    assert all(span["attributes"]["service.name"] == "hub-eu" for span in payload["spans"])


def test_cli_otel_filter_narrows_and_reports_the_exclusions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(out), "--filter", "B"])

    assert exit_code == 0
    assert "1 trace(s)" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["trace_count"] == 1
    assert payload["filtered_out_tasks"] == 2


def test_cli_otel_filter_summary_counts_filtered_tasks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--filter", "B"]
    )

    assert exit_code == 0
    assert "2 task(s) filtered out" in capsys.readouterr().out


def test_cli_otel_watch_reexports_for_count_ticks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    out = tmp_path / "spans.json"

    exit_code = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(out),
            "--watch",
            "--count",
            "2",
            "--interval",
            "0.01",
        ]
    )

    assert exit_code == 0
    summaries = capsys.readouterr().out.strip().splitlines()
    assert len(summaries) == 2
    assert all("exported 11 span(s)" in line for line in summaries)
    assert json.loads(out.read_text(encoding="utf-8"))["trace_count"] == 3


def test_cli_otel_watch_stops_on_a_failing_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    slept: list[float] = []
    args = cli.build_parser().parse_args(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "no-such-dir" / "s.json"),
            "--watch",
        ]
    )

    exit_code = cli_causality._watch_otel(args, sleeper=slept.append)

    assert exit_code == 2
    assert slept == []
    assert "cannot write span records" in capsys.readouterr().err


def test_cli_otel_watch_interrupt_is_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    def _interrupt(args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_causality, "_otel_once", _interrupt)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--watch"]
    )

    assert exit_code == 0


def test_cli_otel_watch_refuses_a_non_positive_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        [
            "causality",
            "otel",
            str(db),
            "--out",
            str(tmp_path / "s.json"),
            "--watch",
            "--interval",
            "0",
        ]
    )

    assert exit_code == 2
    assert "--interval must be positive" in capsys.readouterr().err


# --- health mode --------------------------------------------------------------------


def test_cli_health_flags_anomalies_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "eve", "status": "claimed", "paths": [], "worktree": "w"},
        ts=9.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "C"}, ts=99999.0)
    store.close()

    exit_code = cli.main(["causality", "health", str(db)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "# Causal health:" in out
    assert "task=X owner=eve" in out


def test_cli_health_clean_log_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=2.0)
    store.close()

    exit_code = cli.main(["causality", "health", str(db)])

    assert exit_code == 0
    assert "0 anomalies" in capsys.readouterr().out


def test_cli_health_json_carries_the_signals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "health", str(db), "--json", "--stale-after", "1"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["note"] == "recorded-event signals, not verdicts"
    assert payload["stale_after"] == 1.0


def test_cli_health_refuses_peers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, peer = _federated_pair(tmp_path)

    exit_code = cli.main(["causality", "health", str(db), "--peer", f"peer={peer}"])

    assert exit_code == 2
    assert "--peer is not supported" in capsys.readouterr().err


def test_cli_health_refuses_a_non_positive_stale_after(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "health", str(db), "--stale-after", "0"])

    assert exit_code == 2
    assert "--stale-after must be positive" in capsys.readouterr().err


def test_cli_stale_after_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--stale-after", "60"])

    assert exit_code == 2
    assert "--stale-after belongs to the health mode" in capsys.readouterr().err


def test_cli_health_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "health", str(tmp_path / "absent.db")])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def _seed_clean_lifecycle(db: Path) -> None:
    """One task claimed and released — a healthy log."""
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "B"}, ts=2.0)
    store.close()


def test_cli_health_watch_prints_one_baseline_and_stays_quiet_when_steady(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--watch", "--count", "3", "--interval", "0.01"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.count("# Causal health:") == 1
    assert "orphaned claim seq=" not in out  # the fact format appears only on transitions


def test_cli_health_watch_reports_new_and_cleared_anomalies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "X", "owner": "eve", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()

    def _mutate(_delay: float) -> None:
        # between ticks the orphan X resolves and a fresh orphan Y appears
        added = EventStore(db)
        added.append(EventKind.RELEASE, {"task_id": "X"}, ts=2.0)
        added.append(
            EventKind.CLAIM,
            {"task_id": "Y", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
            ts=3.0,
        )
        added.close()

    args = cli.build_parser().parse_args(
        ["causality", "health", str(db), "--watch", "--count", "2"]
    )
    exit_code = cli_causality._watch_health(args, stale_after=900.0, sleeper=_mutate)

    assert exit_code == 1  # the last tick still sees the Y orphan
    out_lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("# Causal health:") for line in out_lines) == 1
    assert any(
        line.startswith("+ orphaned claim") and "task=Y owner=bob" in line for line in out_lines
    )
    assert any(
        line.startswith("- orphaned claim") and "task=X owner=eve" in line for line in out_lines
    )


def test_cli_health_watch_json_streams_one_report_per_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--watch", "--json", "--count", "2", "--interval", "0.01"]
    )

    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["anomaly_count"] == 0 for line in lines)


def test_cli_health_watch_refuses_a_non_positive_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    exit_code = cli.main(["causality", "health", str(db), "--watch", "--interval", "0"])

    assert exit_code == 2
    assert "--interval must be positive" in capsys.readouterr().err


def test_cli_health_watch_missing_store_fails_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["causality", "health", str(tmp_path / "absent.db"), "--watch"])

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_health_watch_interrupt_is_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "hub.db"
    _seed_clean_lifecycle(db)

    def _interrupt(*args: object, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_causality, "_watch_health", _interrupt)

    exit_code = cli.main(["causality", "health", str(db), "--watch"])

    assert exit_code == 0


def test_cli_otel_filter_refuses_an_unrecorded_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "s.json"), "--filter", "NOPE"]
    )

    assert exit_code == 2
    assert "task(s) not recorded in the log: NOPE" in capsys.readouterr().err


def test_cli_otel_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(
        ["causality", "otel", str(tmp_path / "absent.db"), "--out", str(tmp_path / "s.json")]
    )

    assert exit_code == 2
    assert "missing event store" in capsys.readouterr().err


def test_cli_otel_unwritable_out_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "otel", str(db), "--out", str(tmp_path / "no-such-dir" / "s.json")]
    )

    assert exit_code == 2
    assert "cannot write span records" in capsys.readouterr().err


def test_cli_otel_pushes_to_an_endpoint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    pushed: list[tuple[object, str]] = []

    def _fake_push(projection: object, endpoint: str) -> int:
        pushed.append((projection, endpoint))
        return 11

    monkeypatch.setattr("synapse_channel.otel_export.push_projection", _fake_push)

    exit_code = cli.main(["causality", "otel", str(db), "--endpoint", "http://c:4318/v1/traces"])

    assert exit_code == 0
    assert pushed and pushed[0][1] == "http://c:4318/v1/traces"
    assert "to http://c:4318/v1/traces" in capsys.readouterr().out


def test_cli_otel_failed_push_exits_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    def _refuse(projection: object, endpoint: str) -> int:
        raise RuntimeError("OTLP export failed: collector down")

    monkeypatch.setattr("synapse_channel.otel_export.push_projection", _refuse)

    exit_code = cli.main(["causality", "otel", str(db), "--endpoint", "http://c:4318/v1/traces"])

    assert exit_code == 2
    assert "collector down" in capsys.readouterr().err


def test_cli_otel_reports_skipped_taskless_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "B", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.append(EventKind.RELEASE, {}, ts=2.0)
    store.close()

    exit_code = cli.main(["causality", "otel", str(db), "--out", str(tmp_path / "s.json")])

    assert exit_code == 0
    assert "1 taskless event(s) skipped" in capsys.readouterr().out


def test_cli_health_since_bounds_the_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "OLD", "owner": "bob", "status": "claimed", "paths": [], "worktree": "w"},
        ts=10.0,
    )
    store.append(
        EventKind.CLAIM,
        {"task_id": "NEW", "owner": "alice", "status": "claimed", "paths": [], "worktree": "w"},
        ts=500.0,
    )
    store.close()

    full = cli.main(["causality", "health", str(db)])
    full_out = capsys.readouterr().out
    windowed = cli.main(["causality", "health", str(db), "--since", "400"])
    windowed_out = capsys.readouterr().out

    assert full == 1 and "task=OLD" in full_out
    assert windowed == 1  # NEW's orphan is inside the window
    assert "task=OLD" not in windowed_out
    assert "task=NEW" in windowed_out


def test_cli_since_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--since", "1"])

    assert exit_code == 2
    assert "--since belongs to the health mode" in capsys.readouterr().err


def test_cli_health_textfile_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "ORPH", "owner": "a", "status": "claimed", "paths": [], "worktree": "w"},
        ts=10.0,
    )
    store.close()
    out_file = tmp_path / "health.prom"

    exit_code = cli.main(["causality", "health", str(db), "--textfile", str(out_file)])

    assert exit_code == 1  # the orphan is an anomaly
    assert f"causal-health metrics written to {out_file}" in capsys.readouterr().out
    text = out_file.read_text(encoding="utf-8")
    assert 'synapse_causal_health_anomalies{shape="orphaned"} 1' in text


def test_cli_health_textfile_is_refused_outside_health_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(["causality", "causes", str(db), "4", "--textfile", "x.prom"])

    assert exit_code == 2
    assert "--textfile belongs to the health mode" in capsys.readouterr().err


def test_cli_health_textfile_does_not_compose_with_watch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db)

    exit_code = cli.main(
        ["causality", "health", str(db), "--textfile", "x.prom", "--watch", "--count", "1"]
    )

    assert exit_code == 2
    assert "does not compose with --watch" in capsys.readouterr().err


def test_cli_health_textfile_reports_an_unwritable_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        {"task_id": "T", "owner": "a", "status": "claimed", "paths": [], "worktree": "w"},
        ts=1.0,
    )
    store.close()
    blocker = tmp_path / "occupied"
    blocker.write_text("not a dir", encoding="utf-8")

    exit_code = cli.main(["causality", "health", str(db), "--textfile", str(blocker / "out.prom")])

    assert exit_code == 2
    assert "cannot write the textfile metrics" in capsys.readouterr().err
