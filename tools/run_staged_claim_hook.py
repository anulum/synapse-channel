# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — environment-stable staged claim hook runner
"""Run the staged claim gate with the repository's available interpreter."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_OVERRIDE_ENV = "SYNAPSE_STAGED_CLAIM_PYTHON"


class PythonResolutionError(RuntimeError):
    """The staged claim hook cannot resolve an executable interpreter."""


def _executable(path: Path) -> bool:
    """Return whether ``path`` names an executable regular file."""
    return path.is_file() and os.access(path, os.X_OK)


def resolve_python(
    root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
    *,
    fallback: str | Path | None = None,
) -> Path:
    """Resolve the repository interpreter, or the current CI interpreter."""
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

    for candidate in (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ):
        if _executable(candidate):
            return candidate

    candidate = Path(sys.executable if fallback is None else fallback)
    if _executable(candidate):
        return candidate
    raise PythonResolutionError(f"no executable Python interpreter found; fallback was {candidate}")


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPO_ROOT,
    environ: Mapping[str, str] | None = None,
    fallback: str | Path | None = None,
) -> int:
    """Run the packaged staged gate without accepting hook filenames."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(
            "run_staged_claim_hook accepts no filenames; configure pass_filenames: false",
            file=sys.stderr,
        )
        return 2
    try:
        python = resolve_python(root, environ, fallback=fallback)
        completed = subprocess.run(
            [
                str(python),
                "-m",
                "synapse_channel.cli",
                "git-claim-check",
                "--staged",
            ],
            check=False,
        )
    except (OSError, PythonResolutionError) as exc:
        print(f"staged claim hook setup failed: {exc}", file=sys.stderr)
        return 2
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
