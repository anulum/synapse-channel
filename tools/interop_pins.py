# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the official client versions the interop lane is pinned to
"""Read the interop lane's client pins from the files CI installs from.

The pins live only in those files:

* the MCP Python SDK in ``.github/requirements/requirements-dev.txt``;
* the MCP TypeScript SDK in ``clients/interop-mcp-ts/package-lock.json``;
* the A2A SDK in ``.github/requirements/requirements-interop-clients.txt``.

``docs/interop.md`` must name exactly these versions in its matrix, and
``--advisory`` reports a newer upstream release without changing any pin.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "interop.md"
_MATRIX = re.compile(r"<!-- interop-matrix:start -->\n(.*?)<!-- interop-matrix:end -->", re.S)

CLIENTS = {
    "mcp-python-sdk": ("PyPI", "mcp"),
    "mcp-typescript-sdk": ("npm", "@modelcontextprotocol/sdk"),
    "a2a-sdk": ("PyPI", "a2a-sdk"),
}
"""Each client's registry and package name."""


class InteropPinError(ValueError):
    """A pin file or the documented matrix is missing or does not agree."""


def _requirement(path: Path, package: str) -> str:
    match = re.search(
        rf"^{re.escape(package)}==([^\s;\\]+)", path.read_text(encoding="utf-8"), re.M
    )
    if match is None:
        raise InteropPinError(f"{package} is not pinned in {path.name}")
    return match.group(1)


def pinned_hashes(client: str, root: Path = ROOT) -> str:
    """Return the sorted ``sha256:`` hashes pip enforces for a PyPI client's pin."""
    files = {
        "mcp-python-sdk": "requirements-dev.txt",
        "a2a-sdk": "requirements-interop-clients.txt",
    }
    package = CLIENTS[client][1]
    text = (root / ".github" / "requirements" / files[client]).read_text(encoding="utf-8")
    block = re.search(
        rf"^{re.escape(package)}==[^\n]*\n((?:\s+--hash=sha256:[0-9a-f]+[ \\]*\n)+)", text, re.M
    )
    if block is None:
        raise InteropPinError(f"{package} has no hashes in {files[client]}")
    return ",".join(sorted(re.findall(r"sha256:[0-9a-f]+", block.group(1))))


def pins(root: Path = ROOT) -> dict[str, str]:
    """Return every client's pinned version, read from the files CI installs from."""
    lock = json.loads(
        (root / "clients" / "interop-mcp-ts" / "package-lock.json").read_text(encoding="utf-8")
    )
    try:
        typescript = str(lock["packages"]["node_modules/@modelcontextprotocol/sdk"]["version"])
    except KeyError:
        raise InteropPinError("the MCP TypeScript SDK is not in the client's lock") from None
    requirements = root / ".github" / "requirements"
    return {
        "mcp-python-sdk": _requirement(requirements / "requirements-dev.txt", "mcp"),
        "mcp-typescript-sdk": typescript,
        "a2a-sdk": _requirement(requirements / "requirements-interop-clients.txt", "a2a-sdk"),
    }


def documented(doc: Path = DOC) -> dict[str, set[str]]:
    """Return each client's versions as named in the documented matrix."""
    match = _MATRIX.search(doc.read_text(encoding="utf-8"))
    if match is None:
        raise InteropPinError(f"{doc.name} has no interop matrix")
    found: dict[str, set[str]] = {}
    for line in match.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        found.setdefault(cells[0].strip("`"), set()).add(cells[1])
    return found


def check_documented(root: Path = ROOT, doc: Path = DOC) -> None:
    """Refuse a matrix that names another client or version than the pins."""
    pinned = pins(root)
    named = documented(doc)
    if set(named) != set(pinned):
        raise InteropPinError(
            f"the matrix names {sorted(named)} but the lane pins {sorted(pinned)}"
        )
    for client, versions in named.items():
        if versions != {pinned[client]}:
            raise InteropPinError(
                f"the matrix names {client} {sorted(versions)} but the pin is {pinned[client]}"
            )


def advisory(latest: dict[str, str], root: Path = ROOT) -> list[str]:
    """Return one line per client whose latest upstream release differs from its pin."""
    pinned = pins(root)
    return [
        f"{client}: pinned {pinned[client]}, upstream latest {latest[client]}"
        for client in sorted(pinned)
        if client in latest and latest[client] != pinned[client]
    ]


def main(argv: list[str] | None = None) -> int:
    """Check the documented matrix, or print the upstream advisory; never change a pin."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-doc", action="store_true", help="Compare docs/interop.md.")
    parser.add_argument(
        "--advisory",
        type=Path,
        default=None,
        help="JSON file mapping each client to its latest upstream version.",
    )
    args = parser.parse_args(argv)
    if args.check_doc:
        check_documented()
        print("interop matrix matches the pins")
    if args.advisory is not None:
        lines = advisory(json.loads(args.advisory.read_text(encoding="utf-8")))
        for line in lines:
            print(f"::notice title=Interop client update available::{line}")
        if not lines:
            print("every interop client pin is the latest upstream release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
