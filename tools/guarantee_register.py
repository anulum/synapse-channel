# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — generate and verify the capability guarantee register
"""Validate and render how far each capability has come, stage by stage.

Each capability in ``tools/guarantee_register.toml`` moves through five stages:
described, implemented, wired, qualified and released. Every stage needs
evidence that this tool checks in the source tree, without importing project
code:

- described: public documentation pages that exist;
- implemented: package source files that exist;
- wired: a CLI subcommand (the modules that register it with
  ``add_parser``), a console script's entry module, or a named importer, from
  which a chain of run-time imports reaches an implementing module. Imports
  under ``if TYPE_CHECKING:`` do not count; modules named only by a string at
  run time are not seen;
- qualified: a public qualification receipt for one platform and one
  artefact, either a source commit or a wheel;
- released: a passing receipt for the released wheel of that version.

Receipts are maintainer-attested declarations. This tool checks their syntax,
refuses repeated JSON members and checks that they agree with the register.
It does not check the wheel's bytes, its publication, that the source commit
exists or that the tests ran. Those need independent provenance.

A receipt for one platform cannot qualify another, and a receipt for a source
commit cannot qualify a released wheel. The generated JSON and the page table
are rebuilt from the registry; ``--check`` refuses stale output.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 compatibility path.
    import tomli as tomllib  # pragma: no cover

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "tools" / "guarantee_register.toml"
SCHEMA_VERSION = "synapse-guarantee-register.v1"
RECEIPT_SCHEMA = "synapse-qualification-receipt.v1"
RECEIPT_PREFIX = "docs/qualification/"
_PRIVATE = "docs/internal/"
STAGES = ("described", "implemented", "wired", "qualified", "released")
PLATFORMS = frozenset({"linux", "macos", "windows"})
TOP_LEVEL_FIELDS = frozenset({"schema_version", "generated_output", "page", "capability"})
CAPABILITY_FIELDS = frozenset(
    {"id", "title", "described", "implemented", "wired", "qualified", "released"}
)
RECEIPT_FIELDS = frozenset({"schema", "capability", "platform", "artefact", "tests", "result"})
PAGE_START = "<!-- guarantee-register:start -->"
PAGE_END = "<!-- guarantee-register:end -->"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class RegisterError(ValueError):
    """The guarantee register or a receipt it cites is malformed or untrue."""


def load_register(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load the TOML register without importing project code.

    Parameters
    ----------
    path : Path, optional
        Register file; defaults to ``tools/guarantee_register.toml``.

    Returns
    -------
    dict[str, Any]
        Parsed, not yet validated, register document.
    """
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegisterError(f"{label} must be a non-empty string")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RegisterError(f"{label} must be a list")
    return value


def _canonical(text: str, label: str, *, prefix: str) -> str:
    """Require one canonical repository-relative spelling below ``prefix``."""
    parts = text.split("/")
    if text.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise RegisterError(f"{label} must be a canonical repository path below {prefix}: {text}")
    if not text.startswith(prefix):
        raise RegisterError(f"{label} must be a canonical repository path below {prefix}: {text}")
    return text


def _public_file(root: Path, value: object, label: str, *, prefix: str, suffix: str) -> str:
    """Require an existing public file below ``prefix``, also after resolving links."""
    text = _canonical(_text(value, label), label, prefix=prefix)
    if text.startswith(_PRIVATE) or Path(text).suffix != suffix:
        raise RegisterError(f"{label} must be a public {suffix} file: {text}")
    resolved = (root / text).resolve()
    try:
        inside = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RegisterError(f"{label} leaves the repository: {text}") from exc
    if inside.startswith(_PRIVATE):
        raise RegisterError(f"{label} must be a public {suffix} file: {text}")
    if not resolved.is_file():
        raise RegisterError(f"{label} does not exist: {text}")
    return text


def _files(root: Path, value: object, label: str, *, prefix: str, suffix: str) -> list[str]:
    items = [
        _public_file(root, item, f"{label}[{index}]", prefix=prefix, suffix=suffix)
        for index, item in enumerate(_list(value, label))
    ]
    if len(items) != len(set(items)):
        raise RegisterError(f"{label} contains duplicates")
    return items


def registering_modules(root: Path, name: str) -> tuple[str, ...]:
    """Return the package modules that register the CLI subcommand ``name``.

    Parameters
    ----------
    root : Path
        Repository root containing ``src/synapse_channel``.
    name : str
        Subcommand name passed literally to ``add_parser``.

    Returns
    -------
    tuple[str, ...]
        Dotted names of the registering modules, sorted.
    """
    found: set[str] = set()
    package = root / "src" / "synapse_channel"
    for path in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == name
            ):
                found.add(_module_name(path.relative_to(root).as_posix()))
    return tuple(sorted(found))


def console_script_module(root: Path, name: str) -> str | None:
    """Return the module a ``[project.scripts]`` entry runs, or ``None``.

    Parameters
    ----------
    root : Path
        Repository root containing ``pyproject.toml``.
    name : str
        Console script name.

    Returns
    -------
    str or None
        Dotted module of the entry point, when the script is declared.
    """
    with (root / "pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle).get("project", {}).get("scripts", {})
    entry = scripts.get(name)
    return entry.split(":", 1)[0] if isinstance(entry, str) else None


def _module_name(path: str) -> str:
    parts = Path(path).with_suffix("").parts[1:]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def imported_modules(source: Path, module: str) -> frozenset[str]:
    """Return every module an import statement in ``source`` names at run time.

    Imports under ``if TYPE_CHECKING:`` are left out: they never run. Imports
    inside functions are included, because calling the function runs them.

    Parameters
    ----------
    source : Path
        Python file to read.
    module : str
        Dotted name of ``source``, used to resolve relative imports.

    Returns
    -------
    frozenset[str]
        Imported modules, and ``module.name`` for each ``from`` import.
    """
    package = module if source.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    pending: list[ast.AST] = [ast.parse(source.read_text(encoding="utf-8"))]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.If) and _is_type_checking(node):
            pending.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            base = parts[: len(parts) - node.level + 1] if node.level else []
            stem = ".".join([*base, node.module] if node.module else base)
            found.add(stem)
            found.update(f"{stem}.{alias.name}" for alias in node.names)
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(found)


def reaches(root: Path, start: str, targets: frozenset[str]) -> bool:
    """Return whether ``start`` statically imports a target module, directly or through others.

    Parameters
    ----------
    root : Path
        Repository root containing ``src``.
    start : str
        Dotted module to start from.
    targets : frozenset[str]
        Dotted implementing modules.

    Returns
    -------
    bool
        True when a run-time import chain within the package leads to a target.
    """
    seen: set[str] = set()
    pending = [start]
    while pending:
        module = pending.pop()
        if module in targets:
            return True
        base = root / "src" / Path(*module.split("."))
        source = next(
            (path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()),
            None,
        )
        if module in seen or source is None:
            continue
        seen.add(module)
        pending.extend(
            name for name in imported_modules(source, module) if name.startswith("synapse_channel")
        )
    return False


def _wired(root: Path, value: object, label: str, implemented: list[str]) -> list[dict[str, str]]:
    """Validate that each declared entry point reaches an implementing module."""
    targets = frozenset(_module_name(path) for path in implemented)
    entries: list[dict[str, str]] = []
    for index, item in enumerate(_list(value, label)):
        where = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != {"kind", "name"}:
            raise RegisterError(f"{where} must have exactly kind and name")
        kind = _text(item["kind"], f"{where}.kind")
        name = _text(item["name"], f"{where}.name")
        if kind == "cli":
            starts = registering_modules(root, name)
            if not starts:
                raise RegisterError(f"{where}: no CLI subcommand {name!r} is registered")
        elif kind == "console_script":
            module = console_script_module(root, name)
            if module is None:
                raise RegisterError(f"{where}: no console script {name!r} is declared")
            starts = (module,)
        elif kind == "imported_by":
            importer = _public_file(root, name, f"{where}.name", prefix="src/", suffix=".py")
            starts = (_module_name(importer),)
        else:
            raise RegisterError(f"{where}.kind is unknown: {kind}")
        if not any(reaches(root, start, targets) for start in starts):
            raise RegisterError(f"{where}: {name!r} does not reach an implementing module")
        entries.append({"kind": kind, "name": name})
    return entries


def _artefact(value: object, label: str) -> dict[str, str]:
    """Validate one source-commit or wheel artefact description."""
    if not isinstance(value, dict):
        raise RegisterError(f"{label} must be a table")
    kind = value.get("kind")
    if kind == "source" and set(value) == {"kind", "commit"}:
        if not isinstance(value["commit"], str) or not _COMMIT.fullmatch(value["commit"]):
            raise RegisterError(f"{label}.commit must be a 40-hex commit")
        return {"kind": "source", "commit": value["commit"]}
    if kind == "wheel" and set(value) == {"kind", "version", "sha256"}:
        version, digest = value["version"], value["sha256"]
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise RegisterError(f"{label}.version must be MAJOR.MINOR.PATCH")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise RegisterError(f"{label}.sha256 must be a 64-hex digest")
        return {"kind": "wheel", "version": version, "sha256": digest}
    raise RegisterError(f"{label} must be a source commit or a wheel with version and sha256")


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, refusing a member name that appears twice."""
    names = [name for name, _value in pairs]
    if len(names) != len(set(names)):
        raise RegisterError("a receipt repeats a member name; its evidence is ambiguous")
    return dict(pairs)


def read_receipt(root: Path, path: str, capability: str) -> dict[str, Any]:
    """Read and validate one public qualification receipt.

    Parameters
    ----------
    root : Path
        Repository root.
    path : str
        Receipt path below ``docs/qualification/``.
    capability : str
        Capability the receipt must name.

    Returns
    -------
    dict[str, Any]
        The receipt's platform, artefact and tests.

    Raises
    ------
    RegisterError
        If the receipt is missing, malformed, names another capability, cites a
        missing test or did not pass.
    """
    _public_file(root, path, path, prefix=RECEIPT_PREFIX, suffix=".json")
    try:
        receipt = json.loads(
            (root / path).read_text(encoding="utf-8"), object_pairs_hook=_unique_members
        )
    except json.JSONDecodeError as exc:
        raise RegisterError(f"{path} is not JSON: {exc.msg}") from exc
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise RegisterError(f"{path} fields must be exactly {sorted(RECEIPT_FIELDS)}")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["capability"] != capability:
        raise RegisterError(f"{path} is not a {RECEIPT_SCHEMA} receipt for {capability}")
    if not isinstance(receipt["platform"], str) or receipt["platform"] not in PLATFORMS:
        raise RegisterError(f"{path} platform must be one of {sorted(PLATFORMS)}")
    if receipt["result"] != "passed":
        raise RegisterError(f"{path} records no passing qualification")
    tests = _files(root, receipt["tests"], f"{path}.tests", prefix="tests/test_", suffix=".py")
    if not tests:
        raise RegisterError(f"{path} names no test")
    return {
        "platform": receipt["platform"],
        "artefact": _artefact(receipt["artefact"], f"{path}.artefact"),
        "tests": tests,
    }


def _qualified(root: Path, value: object, label: str, capability: str) -> list[dict[str, Any]]:
    """Bind each declared platform to a receipt for that exact platform."""
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, label)):
        where = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != {"platform", "receipt"}:
            raise RegisterError(f"{where} must have exactly platform and receipt")
        receipt = read_receipt(root, _text(item["receipt"], f"{where}.receipt"), capability)
        if item["platform"] != receipt["platform"]:
            raise RegisterError(
                f"{where}: a {receipt['platform']} receipt cannot qualify {item['platform']}"
            )
        rows.append({"receipt": item["receipt"], **receipt})
    return rows


def _released(root: Path, value: object, label: str, capability: str) -> list[dict[str, Any]]:
    """Bind each release to a passing receipt for that version's wheel."""
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, label)):
        where = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != {"version", "receipt"}:
            raise RegisterError(f"{where} must have exactly version and receipt")
        receipt = read_receipt(root, _text(item["receipt"], f"{where}.receipt"), capability)
        artefact = receipt["artefact"]
        if artefact["kind"] != "wheel":
            raise RegisterError(f"{where}: a source-commit receipt cannot qualify a release")
        if artefact["version"] != item["version"]:
            raise RegisterError(
                f"{where}: a {artefact['version']} wheel receipt cannot qualify {item['version']}"
            )
        rows.append({"version": item["version"], "receipt": item["receipt"], **receipt})
    return rows


def _capability(root: Path, item: object, index: int) -> dict[str, Any]:
    """Validate one capability and derive its stages."""
    label = f"capability[{index}]"
    if not isinstance(item, dict) or set(item) != CAPABILITY_FIELDS:
        raise RegisterError(f"{label} fields must be exactly {sorted(CAPABILITY_FIELDS)}")
    identifier = _text(item["id"], f"{label}.id")
    if not _IDENTIFIER.fullmatch(identifier):
        raise RegisterError(f"{label}.id must be lower-case kebab case: {identifier}")
    implemented = _files(
        root,
        item["implemented"],
        f"{label}.implemented",
        prefix="src/synapse_channel/",
        suffix=".py",
    )
    row: dict[str, Any] = {
        "id": identifier,
        "title": _text(item["title"], f"{label}.title"),
        "described": _files(
            root, item["described"], f"{label}.described", prefix="docs/", suffix=".md"
        ),
        "implemented": implemented,
        "wired": _wired(root, item["wired"], f"{label}.wired", implemented),
        "qualified": _qualified(root, item["qualified"], f"{label}.qualified", identifier),
        "released": _released(root, item["released"], f"{label}.released", identifier),
    }
    reached = [stage for stage in STAGES if row[stage]]
    if tuple(reached) != STAGES[: len(reached)]:
        raise RegisterError(f"{label} skips a stage: reached {reached}")
    row["stage"] = reached[-1] if reached else "none"
    return row


def validate_register(raw: dict[str, Any], root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    """Return validated capability rows with derived stages.

    Parameters
    ----------
    raw : dict[str, Any]
        Parsed register document.
    root : Path, optional
        Repository root the evidence is checked against.

    Returns
    -------
    list[dict[str, Any]]
        One row per capability, in register order.

    Raises
    ------
    RegisterError
        On any schema, evidence, wiring, platform or artefact violation.
    """
    if set(raw) != TOP_LEVEL_FIELDS:
        raise RegisterError(f"top-level fields must be exactly {sorted(TOP_LEVEL_FIELDS)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise RegisterError(f"schema_version must be {SCHEMA_VERSION}")
    rows = [
        _capability(root, item, index)
        for index, item in enumerate(_list(raw["capability"], "capability"))
    ]
    if not rows:
        raise RegisterError("capability must not be empty")
    identifiers = [row["id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise RegisterError("capability ids must be unique")
    return rows


def _output(raw: dict[str, Any], key: str, suffix: str, root: Path) -> str:
    """Return a public output path that stays public after resolving links."""
    value = _canonical(_text(raw.get(key), key), key, prefix="docs/")
    if Path(value).suffix != suffix or value.startswith(_PRIVATE):
        raise RegisterError(f"{key} must be a public docs/*{suffix} path")
    try:
        inside = (root / value).resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RegisterError(f"{key} must be a public docs/*{suffix} path") from exc
    if not inside.startswith("docs/") or inside.startswith(_PRIVATE):
        raise RegisterError(f"{key} must be a public docs/*{suffix} path")
    return value


def render_json(raw: dict[str, Any], root: Path = REPO_ROOT) -> bytes:
    """Render the stable generated JSON document.

    Parameters
    ----------
    raw : dict[str, Any]
        Parsed register document.
    root : Path, optional
        Repository root.

    Returns
    -------
    bytes
        Sorted, indented UTF-8 JSON ending in a newline.
    """
    rows = validate_register(raw, root)
    summary = {stage: sum(row["stage"] == stage for row in rows) for stage in ("none", *STAGES)}
    document = {"schema_version": SCHEMA_VERSION, "summary": summary, "capabilities": rows}
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _cell(items: list[str]) -> str:
    return ", ".join(items) if items else "—"


def render_table(raw: dict[str, Any], root: Path = REPO_ROOT) -> str:
    """Render the Markdown table that the page carries between its markers.

    Parameters
    ----------
    raw : dict[str, Any]
        Parsed register document.
    root : Path, optional
        Repository root.

    Returns
    -------
    str
        Marker-delimited table block.
    """
    lines = [
        PAGE_START,
        "| Capability | Stage | Wired through | Qualified (platform: artefact) | Released |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in validate_register(raw, root):
        wired = [f"`{entry['name']}` ({entry['kind']})" for entry in row["wired"]]
        qualified = [
            f"{entry['platform']}: {entry['artefact']['kind']} "
            f"{entry['artefact'].get('commit', entry['artefact'].get('version'))}"
            for entry in row["qualified"]
        ]
        released = [f"{entry['version']} ({entry['platform']})" for entry in row["released"]]
        cells = [row["title"], row["stage"], _cell(wired), _cell(qualified), _cell(released)]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(PAGE_END)
    return "\n".join(lines)


def _page_with_table(raw: dict[str, Any], root: Path) -> tuple[Path, str]:
    page = root / _output(raw, "page", ".md", root)
    text = page.read_text(encoding="utf-8")
    if (
        text.count(PAGE_START) != 1
        or text.count(PAGE_END) != 1
        or text.index(PAGE_START) > text.index(PAGE_END)
    ):
        raise RegisterError(f"{page.relative_to(root)} must contain one ordered marker pair")
    before, rest = text.split(PAGE_START, 1)
    after = rest.split(PAGE_END, 1)[1]
    return page, before + render_table(raw, root) + after


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def update_register(raw: dict[str, Any], root: Path = REPO_ROOT) -> None:
    """Regenerate the JSON output and the page table atomically.

    Parameters
    ----------
    raw : dict[str, Any]
        Parsed register document.
    root : Path, optional
        Repository root.
    """
    rendered = render_json(raw, root)
    page, text = _page_with_table(raw, root)
    _write_atomic(root / _output(raw, "generated_output", ".json", root), rendered)
    _write_atomic(page, text.encode("utf-8"))


def check_register(raw: dict[str, Any], root: Path = REPO_ROOT) -> None:
    """Refuse a stale generated JSON or page table.

    Parameters
    ----------
    raw : dict[str, Any]
        Parsed register document.
    root : Path, optional
        Repository root.

    Raises
    ------
    RegisterError
        If either generated artefact differs from the register.
    """
    target = root / _output(raw, "generated_output", ".json", root)
    page, text = _page_with_table(raw, root)
    if not target.is_file() or target.read_bytes() != render_json(raw, root):
        raise RegisterError(
            f"{target.relative_to(root)} is stale; run tools/guarantee_register.py --update"
        )
    if page.read_text(encoding="utf-8") != text:
        raise RegisterError(
            f"{page.relative_to(root)} table is stale; run tools/guarantee_register.py --update"
        )


def main(argv: list[str] | None = None, root: Path = REPO_ROOT) -> int:
    """Run ``--update`` or ``--check`` and report a refusal on standard error.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments.
    root : Path, optional
        Repository root; the register is read from ``tools/guarantee_register.toml`` below it.

    Returns
    -------
    int
        Zero on success, one on any refusal.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--update", action="store_true", help="regenerate the JSON and page table")
    mode.add_argument("--check", action="store_true", help="verify both are current")
    arguments = parser.parse_args(argv)
    try:
        raw = load_register(root / "tools" / "guarantee_register.toml")
        if arguments.update:
            update_register(raw, root)
        else:
            check_register(raw, root)
    except (OSError, RegisterError, tomllib.TOMLDecodeError) as exc:
        print(f"guarantee-register: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
