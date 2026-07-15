# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity signing key secret-floor regressions (SCH-H-NEW-13)

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.identity_keys import (
    IdentityKeyError,
    generate_signing_key,
    load_signing_key,
    write_signing_key,
)


def test_load_signing_key_round_trips_owner_only_pem(tmp_path: Path) -> None:
    path = tmp_path / "id.key"
    write_signing_key(path, generate_signing_key())
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = load_signing_key(path)
    assert load_signing_key(path).private_bytes_raw() == loaded.private_bytes_raw()


def test_load_signing_key_refuses_world_readable_without_key_material(tmp_path: Path) -> None:
    path = tmp_path / "loose.key"
    path.write_text(
        "-----BEGIN PRIVATE KEY-----\nmust-not-appear\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    path.chmod(0o644)
    with pytest.raises(IdentityKeyError, match="cannot read identity key") as excinfo:
        load_signing_key(path)
    assert "must-not-appear" not in str(excinfo.value)
    assert "chmod 600" in str(excinfo.value)


def test_load_signing_key_refuses_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.key"
    write_signing_key(real, generate_signing_key())
    link = tmp_path / "link.key"
    link.symlink_to(real)
    with pytest.raises(IdentityKeyError, match="cannot read identity key"):
        load_signing_key(link)
