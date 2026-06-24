# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests pinning the package's public export surface (__all__)

from __future__ import annotations

import synapse_channel


def test_every_all_name_is_importable() -> None:
    # A name promised by __all__ that no longer resolves is a broken public surface.
    missing = [name for name in synapse_channel.__all__ if not hasattr(synapse_channel, name)]
    assert not missing, f"names in __all__ but not importable: {missing}"


def test_all_has_no_duplicates() -> None:
    assert len(synapse_channel.__all__) == len(set(synapse_channel.__all__))


def test_no_private_helpers_leak_into_the_public_surface() -> None:
    # Single-underscore internals must not be re-exported; __version__ (dunder) is exempt.
    leaked = [
        name
        for name in synapse_channel.__all__
        if name.startswith("_") and not name.startswith("__")
    ]
    assert not leaked, f"private names leaked into __all__: {leaked}"


def test_version_is_exported_and_nonempty() -> None:
    assert "__version__" in synapse_channel.__all__
    assert isinstance(synapse_channel.__version__, str)
    assert synapse_channel.__version__.strip()
