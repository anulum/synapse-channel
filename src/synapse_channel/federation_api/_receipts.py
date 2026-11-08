# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: receipts and integrity
"""Receipts and integrity: the federation interface names of this capability.

Merkle roots over journals, signed receipt commitments and per-row authentication keys.
Import these names from ``synapse_channel.federation_api``; this module only groups
them.
"""

from __future__ import annotations

from synapse_channel.core.event_row_mac import (
    RowMacKey,
    load_or_create_row_mac_key,
    row_mac_key_path,
)
from synapse_channel.core.merkle import (
    MerkleRoot,
    RunningRoot,
    build_root,
    render_root_markdown,
    root_to_json,
    run_root,
    verify_root,
)
from synapse_channel.core.receipt_signing import (
    check_receipt_merkle_signature,
    load_receipt_signing_key,
    load_receipt_verification_key,
    sign_merkle_commitment,
)

__all__ = [
    "MerkleRoot",
    "RowMacKey",
    "RunningRoot",
    "build_root",
    "check_receipt_merkle_signature",
    "load_or_create_row_mac_key",
    "load_receipt_signing_key",
    "load_receipt_verification_key",
    "render_root_markdown",
    "root_to_json",
    "row_mac_key_path",
    "run_root",
    "sign_merkle_commitment",
    "verify_root",
]
