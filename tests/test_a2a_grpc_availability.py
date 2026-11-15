# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a missing grpcio install is named, not hidden

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

import pytest

from synapse_channel.a2a_grpc import build_grpc_server_credentials


class _RefuseGrpc(importlib.abc.MetaPathFinder):
    """Reject only a fresh grpc import. Every other module keeps its finder."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == "grpc" or fullname.startswith("grpc."):
            raise ImportError(fullname)
        return None


def test_grpc_credentials_name_the_missing_install() -> None:
    """Building server credentials without grpcio names the optional install."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "grpc" or name.startswith("grpc.")
    }
    for name in saved:
        del sys.modules[name]
    finder = _RefuseGrpc()
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(RuntimeError, match="grpcio"):
            build_grpc_server_credentials(certfile="missing.pem", keyfile="missing.pem")
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)
