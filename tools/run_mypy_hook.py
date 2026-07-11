# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — environment-stable whole-tree mypy hook
"""Run whole-tree mypy with a deterministic repository interpreter."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_OVERRIDE_ENV = "SYNAPSE_MYPY_PYTHON"


class PythonResolutionError(RuntimeError):
    """Raised when the hook cannot resolve an executable Python interpreter."""


def _executable(path: Path) -> bool:
    """Return whether ``path`` names an executable regular file."""
    return path.is_file() and os.access(path, os.X_OK)


def resolve_python(
    root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
    *,
    fallback: str | Path | None = None,
) -> Path:
    """Resolve the interpreter that owns the repository's mypy installation.

    An explicit ``SYNAPSE_MYPY_PYTHON`` path is authoritative and fails closed
    when invalid. Otherwise the conventional POSIX and Windows repository
    virtual environments precede the interpreter running this wrapper.
    """
    environment = os.environ if environ is None else environ
    override = environment.get(PYTHON_OVERRIDE_ENV)
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if not _executable(candidate):
            raise PythonResolutionError(
                f"{PYTHON_OVERRIDE_ENV} does not name an executable file: {candidate}"
            )
        return candidate

    candidates = (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if _executable(candidate):
            return candidate

    candidate = Path(sys.executable if fallback is None else fallback)
    if _executable(candidate):
        return candidate
    raise PythonResolutionError(f"no executable Python interpreter found; fallback was {candidate}")


def run_mypy(python: Path, *, root: Path = REPO_ROOT) -> int:
    """Run repository-configured whole-tree mypy and return its exit status."""
    completed = subprocess.run(
        [str(python), "-m", "mypy"],
        cwd=root,
        check=False,
    )
    return completed.returncode


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
    fallback: str | Path | None = None,
) -> int:
    """Resolve the repository interpreter and run an un-narrowed mypy gate."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(
            "run_mypy_hook accepts no filenames; configure pass_filenames: false",
            file=sys.stderr,
        )
        return 2
    try:
        python = resolve_python(root, environ, fallback=fallback)
        return run_mypy(python, root=root)
    except (OSError, PythonResolutionError) as exc:
        print(f"mypy hook setup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
