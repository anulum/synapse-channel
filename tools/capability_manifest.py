# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — generate and verify the README capability snapshot
"""Generate (or verify) the README capability snapshot from the source tree.

The snapshot is a small inventory table — package version, public API exports,
modules, classes, wire message types, CLI subcommands, tests, benchmarks, doc
pages, workflows, and optional-dependency groups — injected into ``README.md``
between two HTML-comment markers. The counts come from static analysis (a TOML
read and ``ast``); no project code is imported.

``--update`` regenerates the snapshot in place and writes a JSON copy.
``--check`` regenerates and fails if the README is out of date, so a stale
inventory cannot ship. Configuration lives in ``tools/capability_manifest.toml``.

Requires Python 3.11+ (``tomllib``).
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 compatibility path.
    import tomli as tomllib  # pragma: no cover

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "tools" / "capability_manifest.toml"

# The metric keys, in the order they appear in the table.
METRIC_ORDER = [
    "version",
    "public_api_exports",
    "package_modules",
    "classes",
    "wire_message_types",
    "cli_subcommands",
    "tests",
    "benchmark_harnesses",
    "documentation_pages",
    "workflows",
    "optional_dependency_groups",
]


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load the manifest configuration."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_pyproject(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Parse ``pyproject.toml`` into a dict."""
    with (root / config["paths"]["pyproject"]).open("rb") as handle:
        return tomllib.load(handle)


def _count_all_exports(init_path: Path) -> int:
    """Count the names in the package ``__all__`` list."""
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target for target in node.targets if isinstance(target, ast.Name)]
        if any(name.id == "__all__" for name in names) and isinstance(node.value, ast.List):
            return len(node.value.elts)
    return 0


def _iter_py_files(root: Path) -> list[Path]:
    """Return the package's Python modules, excluding caches and egg-info."""
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts and ".egg-info" not in str(path)
    ]


def _count_modules(package_root: Path) -> int:
    """Count package modules (excluding ``__init__.py``)."""
    return sum(1 for path in _iter_py_files(package_root) if path.name != "__init__.py")


def _count_classes(package_root: Path) -> int:
    """Count class definitions across the package."""
    total = 0
    for path in _iter_py_files(package_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return total


def _count_message_types(protocol_path: Path) -> int:
    """Count the string constants defined on the ``MessageType`` class."""
    tree = ast.parse(protocol_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MessageType":
            return sum(
                1
                for item in node.body
                if isinstance(item, ast.Assign)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            )
    return 0


def _count_cli_subcommands(package_root: Path) -> int:
    """Count ``add_parser`` calls (one per CLI subcommand) across the CLI modules.

    The subcommands are registered across the ``cli`` entry point and the focused
    ``cli_<group>`` modules it delegates to, so every ``cli*.py`` module in the
    package is scanned rather than the entry point alone.
    """
    total = 0
    for path in sorted(package_root.glob("cli*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
        )
    return total


def _tracked_test_files(tests_root: Path) -> list[Path]:
    """Return Git-tracked test files, or an empty list outside a Git checkout."""
    repo_root = tests_root.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", f"{tests_root.name}/test_*.py"],
            check=False,
            capture_output=True,
            text=True,
        )  # nosec B603,B607
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [repo_root / line for line in result.stdout.splitlines() if line]


def _count_tests(tests_root: Path) -> int:
    """Count test functions across shipped ``test_*.py`` files."""
    total = 0
    paths = _tracked_test_files(tests_root) or sorted(tests_root.glob("test_*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        )
    return total


def _count_benchmarks(benchmarks_root: Path) -> int:
    """Count benchmark harness scripts."""
    return sum(1 for path in benchmarks_root.glob("*.py") if path.name != "__init__.py")


def _count_docs(docs_root: Path) -> int:
    """Count top-level documentation pages (excluding internal notes)."""
    return sum(1 for path in docs_root.glob("*.md"))


def _count_workflows(workflows_root: Path) -> int:
    """Count GitHub Actions workflow files."""
    if not workflows_root.exists():
        return 0
    return sum(1 for _ in workflows_root.glob("*.yml"))


def collect_metrics(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Gather every capability metric from the source tree."""
    paths = config["paths"]
    pyproject = _load_pyproject(root, config)
    project = pyproject["project"]
    return {
        "version": str(project["version"]),
        "public_api_exports": _count_all_exports(root / paths["init_module"]),
        "package_modules": _count_modules(root / paths["package_root"]),
        "classes": _count_classes(root / paths["package_root"]),
        "wire_message_types": _count_message_types(root / paths["protocol_module"]),
        "cli_subcommands": _count_cli_subcommands(root / paths["package_root"]),
        "tests": _count_tests(root / paths["tests_root"]),
        "benchmark_harnesses": _count_benchmarks(root / paths["benchmarks_root"]),
        "documentation_pages": _count_docs(root / paths["docs_root"]),
        "workflows": _count_workflows(root / paths["workflows_root"]),
        "optional_dependency_groups": len(project.get("optional-dependencies", {})),
    }


def render_block(metrics: dict[str, Any], config: dict[str, Any]) -> str:
    """Render the full snapshot block, markers included."""
    labels = config["labels"]
    start = config["readme"]["marker_start"]
    end = config["readme"]["marker_end"]
    rows = "\n".join(f"| {labels[key]} | {metrics[key]} |" for key in METRIC_ORDER)
    return (
        f"{start}\n"
        "<!-- Generated by tools/capability_manifest.py; do not edit counts by hand. -->\n\n"
        f"### {config['project_label']} capability inventory\n\n"
        "| Surface | Current inventory |\n"
        "|---|---:|\n"
        f"{rows}\n\n"
        "This snapshot is a static inventory generated from the source tree. "
        "Performance and coverage claims have their own committed evidence — see "
        "`VALIDATION.md` and `benchmarks/`.\n"
        f"{end}"
    )


def _extract_region(text: str, start: str, end: str) -> str:
    """Return the marker-to-marker region currently in ``text``."""
    start_idx = text.find(start)
    end_idx = text.find(end)
    if start_idx == -1 or end_idx == -1:
        raise ValueError("README is missing the capability-snapshot markers.")
    return text[start_idx : end_idx + len(end)]


def inject(text: str, block: str, start: str, end: str) -> str:
    """Replace the marker-to-marker region of ``text`` with ``block``."""
    start_idx = text.find(start)
    end_idx = text.find(end)
    if start_idx == -1 or end_idx == -1:
        raise ValueError("README is missing the capability-snapshot markers.")
    return text[:start_idx] + block + text[end_idx + len(end) :]


def write_json(root: Path, config: dict[str, Any], metrics: dict[str, Any]) -> Path:
    """Write the machine-readable manifest and return its path."""
    out = root / str(config["paths"]["json_output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": config["schema_version"],
        "project": config["project_label"],
        "metrics": {key: metrics[key] for key in METRIC_ORDER},
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def update(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Regenerate the README snapshot and the JSON manifest."""
    metrics = collect_metrics(root, config)
    block = render_block(metrics, config)
    readme = root / config["readme"]["path"]
    text = readme.read_text(encoding="utf-8")
    readme.write_text(
        inject(text, block, config["readme"]["marker_start"], config["readme"]["marker_end"]),
        encoding="utf-8",
    )
    write_json(root, config, metrics)
    return metrics


def check(root: Path, config: dict[str, Any]) -> bool:
    """Return whether the README snapshot matches the source tree."""
    metrics = collect_metrics(root, config)
    block = render_block(metrics, config)
    readme = (root / config["readme"]["path"]).read_text(encoding="utf-8")
    current = _extract_region(
        readme, config["readme"]["marker_start"], config["readme"]["marker_end"]
    )
    return current == block


def main(argv: list[str] | None = None, root: Path = REPO_ROOT) -> int:
    """Run the manifest tool in ``--update`` or ``--check`` mode."""
    parser = argparse.ArgumentParser(description="Generate or verify the capability snapshot.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--update", action="store_true", help="Regenerate the snapshot in place.")
    group.add_argument("--check", action="store_true", help="Fail if the snapshot is stale.")
    args = parser.parse_args(argv)

    config = load_config()
    if args.update:
        metrics = update(root, config)
        print(
            f"capability snapshot updated ({metrics['tests']} test functions, "
            f"{metrics['package_modules']} modules)"
        )
        return 0

    if check(root, config):
        print("capability snapshot is up to date")
        return 0
    print(
        "capability snapshot is stale; run `python tools/capability_manifest.py --update`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
