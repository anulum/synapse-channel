# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the vendored cockpit build: made on the pinned Node, noticed, checked
"""The packaged cockpit is the pinned Node's build, without maps, with every licence it needs."""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "vendor_cockpit_build.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vendor_cockpit_build", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["vendor_cockpit_build"] = module
    spec.loader.exec_module(module)
    return module


TOOL = _load()

DIST = {
    "index.html": b"<!doctype html><title>cockpit</title>\n",
    "assets/app-1.js": b"console.log(1);\n//# sourceMappingURL=app-1.js.map\n",
    "assets/app-1.js.map": b"{}",
    "assets/app-1.css": b"a{}\n/*# sourceMappingURL=app-1.css.map */",
    "assets/font-1.woff2": b"\x00\x01binary",
}
LOCK = {
    "": {"name": "cockpit"},
    "node_modules/react": {"version": "19.3.1", "license": "MIT"},
    "node_modules/@fontsource/space-grotesk": {"version": "5.3.0", "license": "OFL-1.1"},
    "node_modules/a/node_modules/nested": {"version": "1.0.0", "license": "ISC"},
    "node_modules/vite": {"version": "8.3.6", "license": "MIT", "dev": True},
    "node_modules/typescript": {"version": "6.0.0", "license": "Apache-2.0", "dev": True},
    "node_modules/fsevents": {"version": "2.3.3", "license": "MIT", "devOptional": True},
}
LICENCES = {
    "node_modules/react": "MIT License\n\nCopyright (c) Meta Platforms, Inc. and affiliates.\n",
    "node_modules/@fontsource/space-grotesk": "Copyright 2020 The Space Grotesk Project Authors\n",
    "node_modules/a/node_modules/nested": "ISC License\n",
    "node_modules/vite": "MIT License\n\nCopyright (c) 2019-present VoidZero Inc.\n",
}


def _root(
    tmp_path: Path,
    *,
    workflow: str = 'node-version: "22.23.4"\n',
    lock: Mapping[str, Mapping[str, object]] | None = None,
    licences: Mapping[str, str] | None = None,
) -> Path:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "clients-cockpit.yml").write_text(workflow, encoding="utf-8")
    client = root / "clients" / "cockpit"
    client.mkdir(parents=True)
    packages = LOCK if lock is None else lock
    (client / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages}), encoding="utf-8"
    )
    for key, text in (LICENCES if licences is None else licences).items():
        (client / key).mkdir(parents=True, exist_ok=True)
        (client / key / "LICENSE").write_text(text, encoding="utf-8")
    return root


Calls = list[list[str]]


def _runner(
    *, node: str = "v22.23.4", dist: dict[str, bytes] | None = None
) -> tuple[Callable[[Sequence[str], Path], str], Calls]:
    calls: Calls = []

    def run(command: Sequence[str], cwd: Path) -> str:
        calls.append(list(command))
        if command[0] == "node":
            return node + "\n"
        for name, data in (DIST if dist is None else dist).items():
            path = cwd / "dist" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return ""

    return run, calls


def test_write_vendors_the_pinned_build_without_maps_and_check_then_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    run, calls = _runner()

    assert TOOL.main(["--write"], root=root, runner=run) == 0
    target = root / TOOL.TARGET
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "THIRD_PARTY_NOTICES.md",
        "assets/app-1.css",
        "assets/app-1.js",
        "assets/font-1.woff2",
        "index.html",
    ]
    assert (target / "assets" / "app-1.js").read_bytes() == b"console.log(1);\n"
    assert (target / "assets" / "app-1.css").read_bytes() == b"a{}\n"
    assert (target / "assets" / "font-1.woff2").read_bytes() == b"\x00\x01binary"
    assert calls[:2] == [["node", "--version"], ["npm", "run", "build"]]
    assert TOOL.main(["--check"], root=root, runner=run) == 0
    assert "the vendored cockpit is current (5 files)" in capsys.readouterr().out


def test_the_notice_names_every_bundled_package_and_the_injecting_build_tools(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run, _ = _runner()
    TOOL.main(["--write"], root=root, runner=run)
    notice = (root / TOOL.TARGET / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    headings = [line for line in notice.splitlines() if line.startswith("## ")]
    # Runtime packages, the nested one read from its own path, and Vite, which the build
    # writes into the output; typescript and fsevents are only for development.
    assert headings == [
        "## @fontsource/space-grotesk 5.3.0 — OFL-1.1",
        "## nested 1.0.0 — ISC",
        "## react 19.3.1 — MIT",
        "## vite 8.3.6 — MIT",
    ]
    assert "Copyright (c) Meta Platforms, Inc. and affiliates." in notice
    assert notice.startswith("<!--\nSPDX-License-Identifier: AGPL-3.0-or-later")
    assert "licensed AGPL-3.0-or-later" in notice


def test_a_licence_text_with_backticks_gets_a_longer_fence(tmp_path: Path) -> None:
    lock = {"": {}, "node_modules/odd": {"version": "1.0.0", "license": "MIT"}}
    root = _root(tmp_path, lock=lock, licences={"node_modules/odd": "Use ``` and ```` freely.\n"})
    run, _ = _runner()
    TOOL.main(["--write"], root=root, runner=run)
    notice = (root / TOOL.TARGET / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "`````text\nUse ``` and ```` freely.\n`````\n" in notice


@pytest.mark.parametrize(
    ("lock", "licences", "reason"),
    [
        (
            {"": {}, "node_modules/closed": {"version": "1.0.0", "license": "UNLICENSED"}},
            {"node_modules/closed": "All rights reserved.\n"},
            "closed 1.0.0 is licensed UNLICENSED, which is not on the redistribution allowlist",
        ),
        (
            {"": {}, "node_modules/bare": {"version": "2.0.0"}},
            {},
            "bare 2.0.0 is licensed without a licence, "
            "which is not on the redistribution allowlist",
        ),
        (
            {"": {}, "node_modules/silent": {"version": "3.0.0", "license": "MIT"}},
            {},
            "node_modules/silent ships no licence text "
            "(LICENSE, LICENSE.md, LICENSE.txt, LICENCE, LICENCE.md, license)",
        ),
    ],
)
def test_a_package_that_cannot_be_redistributed_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    lock: Mapping[str, Mapping[str, object]],
    licences: Mapping[str, str],
    reason: str,
) -> None:
    root = _root(tmp_path, lock=lock, licences=licences)
    run, _ = _runner()

    assert TOOL.main(["--write"], root=root, runner=run) == 1
    assert capsys.readouterr().err == f"refused: {reason}\n"
    assert not (root / TOOL.TARGET).exists()


def test_another_node_or_a_missing_pin_is_refused_before_any_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run, calls = _runner(node="v26.13.0")
    assert TOOL.main(["--check"], root=_root(tmp_path), runner=run) == 1
    assert calls == [["node", "--version"]]
    assert capsys.readouterr().err == (
        "refused: node 26.13.0 is not the cockpit CI's pinned node 22.23.4\n"
    )

    unpinned = _root(tmp_path / "unpinned", workflow="jobs: {}\n")
    assert TOOL.main(["--check"], root=unpinned, runner=_runner()[0]) == 1
    workflow = unpinned / TOOL.WORKFLOW
    assert capsys.readouterr().err == f"refused: {workflow} pins no node-version\n"


def test_check_names_every_missing_extra_and_changed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    run, _ = _runner()
    assert TOOL.main(["--check"], root=root, runner=run) == 1  # nothing vendored yet
    assert "missing: index.html" in capsys.readouterr().err

    TOOL.main(["--write"], root=root, runner=run)
    target = root / TOOL.TARGET
    (target / "index.html").write_bytes(b"edited by hand\n")
    (target / "stray.js").write_bytes(b"left over\n")
    (target / "assets" / "app-1.css").unlink()
    capsys.readouterr()

    assert TOOL.main(["--check"], root=root, runner=run) == 1
    assert capsys.readouterr().err.splitlines() == [
        "missing: assets/app-1.css",
        "extra: stray.js",
        "differs: index.html",
        "the vendored cockpit is stale; run: python tools/vendor_cockpit_build.py --write",
    ]


def test_a_failing_command_is_refused_with_its_output(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import sys; print('built badly'); sys.exit(3)"]

    with pytest.raises(TOOL.VendorRefusal) as refusal:
        TOOL._run(command, tmp_path)

    assert "failed in" in str(refusal.value)
    assert "built badly" in str(refusal.value)
    assert TOOL._run([sys.executable, "-c", "print('ok')"], tmp_path) == "ok\n"


def test_the_tool_runs_as_a_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(TOOL_PATH), "--help"])
    with pytest.raises(SystemExit) as finished:
        runpy.run_path(str(TOOL_PATH), run_name="__main__")
    assert finished.value.code == 0


def test_the_committed_build_is_declared_and_its_notice_names_the_runtime_packages() -> None:
    """The repository's own vendored copy: a build entry point, and the notice it must carry."""
    target = ROOT / TOOL.TARGET
    notice = (target / TOOL.NOTICE).read_text(encoding="utf-8")

    assert (target / "index.html").is_file()
    assert not list(target.rglob("*.map"))
    for package in ("react", "react-dom", "scheduler", "vite", "rolldown"):
        assert f"\n## {package} " in notice


def test_a_second_write_replaces_the_vendored_directory_whole(tmp_path: Path) -> None:
    root = _root(tmp_path)
    run, _ = _runner()
    TOOL.main(["--write"], root=root, runner=run)
    stale = root / TOOL.TARGET / "assets" / "app-0.js"
    stale.write_bytes(b"from an older build\n")

    assert TOOL.main(["--write"], root=root, runner=run) == 0
    assert not stale.exists()
    assert TOOL.main(["--check"], root=root, runner=run) == 0


def test_every_vendored_file_is_package_data() -> None:
    """A build that adds a directory must add its pattern, or the wheel would drop its files."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = [
        PurePosixPath(pattern)
        for pattern in pyproject["tool"]["setuptools"]["package-data"]["synapse_channel"]
    ]
    package = ROOT / "src" / "synapse_channel"
    unpackaged = [
        relative
        for path in sorted((ROOT / TOOL.TARGET).rglob("*"))
        if path.is_file()
        for relative in [PurePosixPath(path.relative_to(package).as_posix())]
        # setuptools globs one directory level per "*": compare part by part.
        if not any(
            len(pattern.parts) == len(relative.parts) and relative.match(str(pattern))
            for pattern in patterns
        )
    ]
    assert unpackaged == []
