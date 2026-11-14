# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — vendor the built cockpit into the package, made and checked on the CI's Node
"""Vendor the cockpit's built client into the package, and check that it is current.

The wheel ships the cockpit, so the dashboard opens it with no source checkout,
while the release recipe stays free of Node. This tool builds the client once,
on the Node version the cockpit CI pins, and writes the result into
``src/synapse_channel/dashboard_assets/cockpit/``, which is committed. The
cockpit CI runs it with ``--check``, which fails when a fresh build differs from
the committed copy.

Vendored: every file of ``clients/cockpit/dist`` except the source maps, with
the comment that names a map removed from the end of a script or style sheet;
and ``THIRD_PARTY_NOTICES.md``, which names each npm package the client bundles
(the lock's packages that are not for development only) with its version, its
licence and the licence text the package ships. A package whose licence is not
on the redistribution allowlist, or that ships no licence text, is refused.

Usage::

    python tools/vendor_cockpit_build.py --write
    python tools/vendor_cockpit_build.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT = Path("clients/cockpit")
TARGET = Path("src/synapse_channel/dashboard_assets/cockpit")
WORKFLOW = Path(".github/workflows/clients-cockpit.yml")
NOTICE = "THIRD_PARTY_NOTICES.md"

REDISTRIBUTABLE = frozenset(
    {"0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "ISC", "MIT", "OFL-1.1"}
)
"""SPDX licences whose terms allow the wheel to redistribute a package's built code."""

BUILD_RUNTIME = frozenset({"node_modules/rolldown", "node_modules/vite"})
"""Build tools the lock marks for development whose own code the build writes into the output.

Vite adds its module-preload helper to the entry script (``vite:preloadError``
is in it), and the bundler adds the helpers that join the CommonJS packages
(React ships CommonJS) to the modules. Their notices are shipped with the rest.
"""

LICENCE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "license")
"""File names, in order, under which a package ships its licence text."""

_BACKTICKS = re.compile(r"`+")
_PIN = re.compile(r'node-version:\s*"(?P<version>\d+\.\d+\.\d+)"')
_MAP_COMMENT = re.compile(
    rb"\n?(?://# sourceMappingURL=[^\n]*|/\*# sourceMappingURL=[^\n]*?\*/)\s*$"
)
_HEADER = """<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->
"""

Runner = Callable[[Sequence[str], Path], str]
"""Run a command in a directory and return its standard output; raise on failure."""


class VendorRefusal(Exception):
    """A reason the build cannot be vendored, stated for the person who runs the tool."""


def _run(command: Sequence[str], cwd: Path) -> str:
    """Run ``command`` in ``cwd`` and return its output, or refuse with its error."""
    done = subprocess.run(  # nosec B603 - fixed argument vectors, no shell
        list(command), cwd=cwd, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise VendorRefusal(f"{' '.join(command)} failed in {cwd}:\n{done.stdout}{done.stderr}")
    return done.stdout


def pinned_node(workflow: Path) -> str:
    """Return the Node version the cockpit CI pins, read from its workflow."""
    found = _PIN.search(workflow.read_text(encoding="utf-8"))
    if found is None:
        raise VendorRefusal(f"{workflow} pins no node-version")
    return found["version"]


def require_node(pin: str, runner: Runner, cwd: Path) -> None:
    """Refuse unless the ``node`` on the path is exactly the pinned version."""
    running = runner(["node", "--version"], cwd).strip().removeprefix("v")
    if running != pin:
        raise VendorRefusal(f"node {running} is not the cockpit CI's pinned node {pin}")


def vendored_files(dist: Path) -> dict[str, bytes]:
    """Return the build's files without source maps, keyed by their path in the build."""
    files: dict[str, bytes] = {}
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.suffix == ".map":
            continue
        data = path.read_bytes()
        if path.suffix in (".js", ".css"):
            data = _MAP_COMMENT.sub(b"\n", data)
        files[path.relative_to(dist).as_posix()] = data
    return files


Package = tuple[str, str, str, str]
"""``(path in the lock, name, version, licence)`` of one bundled package."""


def bundled_packages(lock: Path) -> list[Package]:
    """Return each package of the lock the build output carries code of, by name.

    That is every package not only for development, and the build tools of
    :data:`BUILD_RUNTIME`.
    """
    packages = json.loads(lock.read_text(encoding="utf-8")).get("packages", {})
    bundled = []
    for key, entry in packages.items():
        development = entry.get("dev") or entry.get("devOptional")
        if not key or (development and key not in BUILD_RUNTIME):
            continue
        name = key.rpartition("node_modules/")[2]
        bundled.append((key, name, str(entry.get("version", "")), str(entry.get("license", ""))))
    return sorted(bundled, key=lambda package: (package[1], package[0]))


def licence_text(client: Path, key: str) -> str:
    """Return the licence text the package at ``key`` ships, or refuse when it ships none."""
    for file_name in LICENCE_FILES:
        candidate = client / key / file_name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    raise VendorRefusal(f"{key} ships no licence text ({', '.join(LICENCE_FILES)})")


def notice(packages: list[Package], client: Path) -> bytes:
    """Return the third-party notice for the bundled packages, refusing any licence off the list."""
    sections = []
    for key, name, version, licence in packages:
        if licence not in REDISTRIBUTABLE:
            raise VendorRefusal(
                f"{name} {version} is licensed {licence or 'without a licence'}, "
                "which is not on the redistribution allowlist"
            )
        text = licence_text(client, key)
        # A fence longer than any run of backticks in the text, so no text can close it.
        fence = "`" * max(3, max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
        sections.append(f"## {name} {version} — {licence}\n\n{fence}text\n{text}\n{fence}\n")
    head = (
        "# Third-party notices for the packaged cockpit\n\n"
        "The cockpit in this directory is built from `clients/cockpit`, licensed "
        "AGPL-3.0-or-later. Its bundle includes the npm packages below, each under its "
        "own licence, whose text is reproduced as the package ships it.\n\n"
    )
    return (_HEADER + "\n" + head + "\n".join(sections)).encode("utf-8")


def expected_tree(root: Path, runner: Runner = _run) -> dict[str, bytes]:
    """Build the client on the pinned Node and return every file the package should carry."""
    client = root / CLIENT
    require_node(pinned_node(root / WORKFLOW), runner, client)
    runner(["npm", "run", "build"], client)
    tree = vendored_files(client / "dist")
    tree[NOTICE] = notice(bundled_packages(client / "package-lock.json"), client)
    return tree


def committed_tree(target: Path) -> dict[str, bytes]:
    """Return the vendored files as committed, keyed by their path."""
    if not target.is_dir():
        return {}
    return {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }


def differences(committed: dict[str, bytes], expected: dict[str, bytes]) -> list[str]:
    """Return one line per file that is missing, extra or different."""
    lines = [f"missing: {name}" for name in sorted(expected.keys() - committed.keys())]
    lines += [f"extra: {name}" for name in sorted(committed.keys() - expected.keys())]
    lines += [
        f"differs: {name}"
        for name in sorted(expected.keys() & committed.keys())
        if expected[name] != committed[name]
    ]
    return lines


def write_tree(target: Path, tree: dict[str, bytes]) -> None:
    """Replace the vendored directory with ``tree``."""
    if target.exists():
        shutil.rmtree(target)
    for name, data in tree.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main(
    argv: Sequence[str] | None = None, *, root: Path = REPO_ROOT, runner: Runner = _run
) -> int:
    """Write or check the vendored cockpit; return 0 when it is current, 1 otherwise."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="replace the vendored build")
    mode.add_argument("--check", action="store_true", help="fail when the vendored build is stale")
    arguments = parser.parse_args(argv)
    try:
        expected = expected_tree(root, runner)
    except VendorRefusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1
    target = root / TARGET
    if arguments.write:
        write_tree(target, expected)
        print(f"vendored {len(expected)} files into {TARGET}")
        return 0
    found = differences(committed_tree(target), expected)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(
            "the vendored cockpit is stale; run: python tools/vendor_cockpit_build.py --write",
            file=sys.stderr,
        )
        return 1
    print(f"the vendored cockpit is current ({len(expected)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
