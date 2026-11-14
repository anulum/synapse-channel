# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one disposable home for POSIX and Windows tests
"""Aim ``~`` at a disposable directory on every platform a test runs on."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest


def redirect_home(environment: Mapping[str, str], home: str | Path) -> dict[str, str]:
    """Return a copy of ``environment`` whose ``~`` is ``home``.

    POSIX expands ``~`` through ``HOME``. Windows expands it through
    ``USERPROFILE``. A child that keeps the real ``USERPROFILE`` still reads
    and writes the operator profile when a test replaces only ``HOME``.

    Parameters
    ----------
    environment : Mapping[str, str]
        Base environment. It is not modified.
    home : str or pathlib.Path
        Disposable directory both variables should name.

    Returns
    -------
    dict[str, str]
        The copy, with ``HOME`` and ``USERPROFILE`` set to the same path.
    """
    redirected = dict(environment)
    text = os.fspath(home)
    redirected["HOME"] = text
    redirected["USERPROFILE"] = text
    return redirected


def bind_home(monkeypatch: pytest.MonkeyPatch, home: str | Path) -> None:
    """Point the current process's ``~`` at ``home`` until the test ends.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture that restores the environment afterwards.
    home : str or pathlib.Path
        Disposable directory both variables should name.
    """
    text = os.fspath(home)
    monkeypatch.setenv("HOME", text)
    monkeypatch.setenv("USERPROFILE", text)
