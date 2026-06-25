# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the README capability-manifest tool

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "capability_manifest.py"
_SPEC = importlib.util.spec_from_file_location("capability_manifest", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
cap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cap)


def _write_manifest_repo(root: Path) -> None:
    (root / "src" / "synapse_channel" / "core").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "benchmarks").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n[project.optional-dependencies]\ndev = []\n',
        encoding="utf-8",
    )
    (root / "src" / "synapse_channel" / "__init__.py").write_text(
        '__all__ = ["Thing"]\n',
        encoding="utf-8",
    )
    (root / "src" / "synapse_channel" / "module.py").write_text(
        "class Thing:\n    pass\n",
        encoding="utf-8",
    )
    (root / "src" / "synapse_channel" / "cli.py").write_text(
        'subparsers.add_parser("run")\n',
        encoding="utf-8",
    )
    (root / "src" / "synapse_channel" / "core" / "protocol.py").write_text(
        'class MessageType:\n    CHAT = "chat"\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "benchmarks" / "sample_benchmark.py").write_text(
        "def run():\n    return None\n",
        encoding="utf-8",
    )
    (root / "docs" / "usage.md").write_text("# Usage\n", encoding="utf-8")
    (root / "README.md").write_text(
        "before\n"
        f"{cap.load_config()['readme']['marker_start']}\n"
        "stale\n"
        f"{cap.load_config()['readme']['marker_end']}\n"
        "after\n",
        encoding="utf-8",
    )


def test_load_config_has_sections() -> None:
    config = cap.load_config()
    assert config["project_label"] == "SYNAPSE CHANNEL"
    assert {"paths", "readme", "labels"} <= set(config)


def test_collect_metrics_structure() -> None:
    config = cap.load_config()
    metrics = cap.collect_metrics(cap.REPO_ROOT, config)
    assert set(metrics) == set(cap.METRIC_ORDER)
    assert isinstance(metrics["version"], str)
    for key in cap.METRIC_ORDER:
        if key != "version":
            assert isinstance(metrics[key], int)
            assert metrics[key] >= 1


def test_render_block_contains_markers_and_labels() -> None:
    config = cap.load_config()
    metrics = cap.collect_metrics(cap.REPO_ROOT, config)
    block = cap.render_block(metrics, config)
    assert config["readme"]["marker_start"] in block
    assert config["readme"]["marker_end"] in block
    assert "capability inventory" in block
    for label in config["labels"].values():
        assert label in block


def test_inject_replaces_region() -> None:
    text = "before\n<!--S-->\nold\n<!--E-->\nafter"
    out = cap.inject(text, "<!--S-->\nnew\n<!--E-->", "<!--S-->", "<!--E-->")
    assert "old" not in out
    assert "new" in out
    assert out.startswith("before")
    assert out.endswith("after")


def test_inject_missing_markers_raises() -> None:
    with pytest.raises(ValueError, match="markers"):
        cap.inject("no markers here", "x", "<!--S-->", "<!--E-->")


def test_extract_region_missing_raises() -> None:
    with pytest.raises(ValueError, match="markers"):
        cap._extract_region("nothing here", "<!--S-->", "<!--E-->")


def test_check_is_up_to_date() -> None:
    config = cap.load_config()
    assert cap.check(cap.REPO_ROOT, config) is True


def test_write_json(tmp_path: Path) -> None:
    config = cap.load_config()
    config["paths"]["json_output"] = "manifest.json"
    metrics = cap.collect_metrics(cap.REPO_ROOT, config)
    out = cap.write_json(tmp_path, config, metrics)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["project"] == "SYNAPSE CHANNEL"
    assert set(data["metrics"]) == set(cap.METRIC_ORDER)


def test_main_check_returns_zero() -> None:
    assert cap.main(["--check"]) == 0


def test_main_update_branch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_manifest_repo(tmp_path)
    assert cap.main(["--update"], root=tmp_path) == 0
    assert "updated" in capsys.readouterr().out
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "capability inventory" in readme
    assert (tmp_path / "docs" / "_generated" / "capability_manifest.json").exists()


def test_main_check_stale_returns_one(tmp_path: Path) -> None:
    _write_manifest_repo(tmp_path)
    assert cap.main(["--check"], root=tmp_path) == 1


def test_count_all_exports_zero_without_all(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text("x = 1\n", encoding="utf-8")
    assert cap._count_all_exports(module) == 0


def test_count_message_types_zero_without_class(tmp_path: Path) -> None:
    module = tmp_path / "p.py"
    module.write_text("y = 2\n", encoding="utf-8")
    assert cap._count_message_types(module) == 0


def test_count_workflows_zero_when_absent(tmp_path: Path) -> None:
    assert cap._count_workflows(tmp_path / "missing") == 0


def test_update_is_idempotent_when_current() -> None:
    config = cap.load_config()
    readme = cap.REPO_ROOT / config["readme"]["path"]
    before = readme.read_text(encoding="utf-8")
    metrics = cap.update(cap.REPO_ROOT, config)
    assert readme.read_text(encoding="utf-8") == before  # already current -> no change
    assert metrics["version"]
