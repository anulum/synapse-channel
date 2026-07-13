# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from pathlib import Path

import pytest

from synapse_channel.apply_patch_paths import ApplyPatchPathError, parse_apply_patch_paths


def test_extracts_all_unique_source_and_destination_paths() -> None:
    patch = """*** Begin Patch
*** Add File: new.py
+new
*** Update File: old.py
*** Move to: moved.py
@@
-old
+new
*** Delete File: gone.py
*** End Patch"""
    assert parse_apply_patch_paths(patch) == (
        Path("new.py"),
        Path("old.py"),
        Path("moved.py"),
        Path("gone.py"),
    )


@pytest.mark.parametrize(
    "patch",
    [
        "not a patch",
        "*** Begin Patch\n*** Unsupported: x\n*** End Patch",
        "*** Begin Patch\n*** Add File:  x\n*** End Patch",
        "*** Begin Patch\n@@\n+x\n*** End Patch",
    ],
)
def test_malformed_or_targetless_patch_fails_closed(patch: str) -> None:
    with pytest.raises(ApplyPatchPathError):
        parse_apply_patch_paths(patch)
