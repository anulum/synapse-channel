# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher optional-extra absence path (real isolated process)
"""Fail-closed path when the SQLCipher driver is not importable.

Uses a real ``python -S`` subprocess with only the source tree on ``PYTHONPATH``
so site-packages (including ``sqlcipher3-binary``) are not loaded. No mocks.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in an isolated interpreter without site-packages."""
    return subprocess.run(
        [sys.executable, "-S", "-c", textwrap.dedent(code)],
        env={"PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_import_sqlcipher_fails_closed_without_site_packages() -> None:
    """Isolated interpreter without site-packages cannot load SQLCipher."""
    result = _run_isolated(
        """
        from synapse_channel.core.persistence_sqlcipher import (
            SQLCIPHER_INSTALL_HINT,
            SqlCipherUnavailableError,
            import_sqlcipher_module,
        )
        try:
            import_sqlcipher_module()
        except SqlCipherUnavailableError as exc:
            text = str(exc)
            assert "sqlcipher" in text.lower()
            assert "pip install" in text
            assert (
                SQLCIPHER_INSTALL_HINT in text
                or "synapse-channel[sqlcipher]" in text
            )
            raise SystemExit(0)
        raise SystemExit("import_sqlcipher_module should have raised")
        """
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r} rc={result.returncode}"
    )


def test_connect_sqlcipher_fails_closed_in_isolated_process(tmp_path: Path) -> None:
    """connect_sqlcipher with a key fails closed when the driver is absent."""
    db_path = str(tmp_path / "x.db")
    result = _run_isolated(
        f"""
        from pathlib import Path
        from synapse_channel.core.persistence_sqlcipher import (
            SqlCipherUnavailableError,
            connect_sqlcipher,
        )
        try:
            connect_sqlcipher(Path({db_path!r}), b"\\x00" * 32)
        except SqlCipherUnavailableError as exc:
            assert "synapse-channel[sqlcipher]" in str(exc)
            raise SystemExit(0)
        raise SystemExit("connect_sqlcipher should have raised")
        """
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r} rc={result.returncode}"
    )
