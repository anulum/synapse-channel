# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — build one commit's release payload the same way on any host
"""Build the wheel and the source archive of one commit, byte for byte the same on any host.

The release workflow and a consumer who wants to reproduce a release run this one
recipe::

    python tools/build_release_payload.py --out DIR [--ref REF] [--repository PATH] [--work DIR]

1. ``REF`` (default ``HEAD``) is resolved to a commit of the repository.
2. The commit's tree is exported into a fresh directory through a private index,
   under umask 0022, so neither the caller's checkout nor its umask reaches the
   build: the wheel carries the file modes of the tree it is built from.
3. The running ``build``, ``setuptools`` and ``wheel`` must equal the pins of that
   tree's ``.github/requirements/requirements-tools.txt``; anything else is refused
   before a build, because the wheel names the backend that made it.
4. The build runs without isolation, with ``SOURCE_DATE_EPOCH`` set to the commit
   time, and the source archive is repacked by ``tools/repack_sdist.py``.
5. ``DIR`` receives exactly one wheel, one source archive and ``SHA256SUMS`` for the
   two, in the format of ``sha256sum``.

The path, time zone and locale of the host and Python 3.12 or 3.13 do not change
the bytes; the backend version and the umask do. Uncommitted changes are never
built: the export reads the commit, not the working tree.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess  # nosec B404 # fixed argv only, never a shell
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repack_sdist import repack_sdist

PINNED_TOOLS = ("build", "setuptools", "wheel")
"""The build frontend and backend whose versions the payload depends on."""

REQUIREMENTS = ".github/requirements/requirements-tools.txt"
"""The hash-pinned file, inside the built tree, that names their versions."""

BUILD_UMASK = 0o022
"""The umask of the export and the build; the wheel records the modes it yields."""

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.MULTILINE)


class ReleasePayloadError(Exception):
    """The payload cannot be built as the recipe requires."""


def _git(
    repository: Path, *args: str, index: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one fixed ``git -C <repository>`` command, optionally against a private index."""
    git = shutil.which("git")
    if git is None:
        raise ReleasePayloadError("git is not on PATH")
    environment = dict(os.environ)
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(  # nosec B603 # fixed git argv, no shell
        [git, "-C", str(repository), *args],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def resolve_commit(repository: Path, ref: str) -> tuple[str, int]:
    """Return the commit ``ref`` names in ``repository`` and its commit time in epoch seconds.

    Raises
    ------
    ReleasePayloadError
        When ``ref`` names no commit there.
    """
    shown = _git(repository, "log", "-1", "--format=%H %ct", f"{ref}^{{commit}}", "--")
    fields = shown.stdout.split()
    if shown.returncode != 0 or len(fields) != 2 or not fields[1].isdigit():
        raise ReleasePayloadError(f"the ref {ref!r} does not name a commit in {repository}")
    return fields[0], int(fields[1])


def export_tree(repository: Path, commit: str, destination: Path, index: Path) -> None:
    """Write the tree of ``commit`` into the new directory ``destination`` through ``index``.

    The caller's own index and working tree are never read or written.

    Raises
    ------
    ReleasePayloadError
        When git cannot read the tree or write it out.
    """
    destination.mkdir()
    for args in (
        ("read-tree", commit),
        ("checkout-index", "--all", f"--prefix={destination}{os.sep}"),
    ):
        done = _git(repository, *args, index=index)
        if done.returncode != 0:
            detail = done.stderr.strip() or done.stdout.strip()
            raise ReleasePayloadError(f"git {args[0]} failed for {commit}: {detail}")


def pinned_versions(requirements: str) -> dict[str, str]:
    """Return the ``name==version`` pins of a requirements text, names lower-cased."""
    return {name.lower(): version for name, version in _PIN.findall(requirements)}


def check_tools(
    tree: Path, commit: str, installed: Callable[[str], str] = importlib.metadata.version
) -> dict[str, str]:
    """Return the pinned versions of the build tools, or refuse if the running ones differ.

    Raises
    ------
    ReleasePayloadError
        When the tree has no pins file, pins no version of a tool, or the running
        interpreter carries another version.
    """
    path = tree / REQUIREMENTS
    if not path.is_file():
        raise ReleasePayloadError(f"{REQUIREMENTS} is missing at {commit}")
    pins = pinned_versions(path.read_text(encoding="utf-8"))
    missing = [name for name in PINNED_TOOLS if name not in pins]
    if missing:
        raise ReleasePayloadError(f"{REQUIREMENTS} pins no version of {', '.join(missing)}")
    differ = []
    for name in PINNED_TOOLS:
        try:
            version = installed(name)
        except importlib.metadata.PackageNotFoundError:
            differ.append(f"{name} is not installed, {pins[name]} pinned")
            continue
        if version != pins[name]:
            differ.append(f"{name} {version} is installed, {pins[name]} pinned")
    if differ:
        raise ReleasePayloadError(
            f"the build tools differ from the pins of {commit}: {'; '.join(differ)}; "
            f"install them with: python -m pip install --require-hashes -r {REQUIREMENTS}"
        )
    return {name: pins[name] for name in PINNED_TOOLS}


def run_build(tree: Path, raw: Path, epoch: int, log: Path) -> tuple[Path, Path]:
    """Build ``tree`` into ``raw`` without isolation and return its one wheel and one sdist.

    Raises
    ------
    ReleasePayloadError
        When the build fails or does not produce exactly one of each.
    """
    environment = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    command = [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(raw), str(tree)]
    with log.open("w", encoding="utf-8") as output:
        done = subprocess.run(  # nosec B603 # fixed argv, no shell
            command, cwd=tree, env=environment, stdout=output, stderr=subprocess.STDOUT, check=False
        )
    if done.returncode != 0:
        raise ReleasePayloadError(
            f"the build failed (exit {done.returncode}); its output is in {log}"
        )
    wheels, sdists = sorted(raw.glob("*.whl")), sorted(raw.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleasePayloadError(
            f"the build produced {len(wheels)} wheel(s) and {len(sdists)} source archive(s); "
            "expected one of each"
        )
    return wheels[0], sdists[0]


def digest_lines(files: Sequence[Path]) -> list[str]:
    """Return ``sha256sum`` lines for ``files``, sorted by file name."""
    lines = []
    for path in sorted(files, key=lambda item: item.name):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    return lines


def build_payload(repository: Path, ref: str, out: Path, work: Path) -> list[str]:
    """Build the payload of ``ref`` into ``out``, scratch in ``work``; return its digest lines.

    Raises
    ------
    ReleasePayloadError
        When any step of the recipe cannot be followed.
    """
    for label, directory in (("output", out), ("scratch", work)):
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ReleasePayloadError(f"the {label} directory {directory} is not empty")
    work.mkdir(parents=True, exist_ok=True)
    commit, epoch = resolve_commit(repository, ref)
    previous = os.umask(BUILD_UMASK)
    try:
        tree = work / "tree"
        export_tree(repository, commit, tree, work / "export.index")
        tools = check_tools(tree, commit)
        raw = work / "raw-dist"
        wheel, sdist = run_build(tree, raw, epoch, work / "build.log")
        out.mkdir(parents=True, exist_ok=True)
        payload_wheel = out / wheel.name
        shutil.copyfile(wheel, payload_wheel)
        payload_sdist = out / sdist.name
        repack_sdist(sdist, payload_sdist, source_date_epoch=epoch)
    finally:
        os.umask(previous)
    lines = digest_lines([payload_wheel, payload_sdist])
    (out / "SHA256SUMS").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    print(f"commit {commit}, SOURCE_DATE_EPOCH {epoch}")
    print(", ".join(f"{name} {version}" for name, version in tools.items()))
    for line in lines:
        print(line)
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True, type=Path, help="New or empty output directory.")
    parser.add_argument("--ref", default="HEAD", help="The commit, tag or branch to build.")
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="The repository to read the commit from; defaults to this tool's checkout.",
    )
    parser.add_argument(
        "--work", type=Path, help="Directory for a fresh scratch folder; the system's by default."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build the payload and return a shell status: 0 built, 1 refused."""
    arguments = _parser().parse_args(argv)
    if arguments.work is not None:
        arguments.work.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="release-payload-", dir=arguments.work) as scratch:
            build_payload(arguments.repository, arguments.ref, arguments.out, Path(scratch))
    except ReleasePayloadError as exc:
        print(f"build_release_payload: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
