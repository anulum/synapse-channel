# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher missing-driver / install-hint tests (no native dep)

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from synapse_channel.core.persistence_sqlcipher import (
    SQLCIPHER_INSTALL_HINT,
    SqlCipherUnavailableError,
    connect_sqlcipher,
    import_sqlcipher_module,
)


def test_connect_sqlcipher_raises_clear_hint_without_driver() -> None:
    with patch(
        "synapse_channel.core.persistence_sqlcipher.import_sqlcipher_module",
        side_effect=SqlCipherUnavailableError(SQLCIPHER_INSTALL_HINT),
    ):
        with pytest.raises(SqlCipherUnavailableError, match="synapse-channel\\[sqlcipher\\]"):
            connect_sqlcipher(Path(":memory:"), b"\x00" * 32)


def test_install_hint_mentions_extra() -> None:
    assert "sqlcipher" in SQLCIPHER_INSTALL_HINT.lower()
    assert "pip install" in SQLCIPHER_INSTALL_HINT


def test_import_sqlcipher_module_surfaces_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name in {"sqlcipher3", "pysqlcipher3"} or name.startswith(
            ("sqlcipher3.", "pysqlcipher3.")
        ):
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(SqlCipherUnavailableError, match="sqlcipher"):
        import_sqlcipher_module()
