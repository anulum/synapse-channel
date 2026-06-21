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


def test_main_update_branch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cap, "update", lambda root, config: {"tests": 1, "package_modules": 1})
    assert cap.main(["--update"]) == 0
    assert "updated" in capsys.readouterr().out


def test_main_check_stale_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap, "check", lambda root, config: False)
    assert cap.main(["--check"]) == 1


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
