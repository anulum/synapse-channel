# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — legacy import compatibility for kernel relay helpers
"""Compatibility facade for the kernel-owned relay helpers.

The implementation lives in :mod:`synapse_channel.core.relay` so the
coordination kernel has no upward import. This module intentionally re-exports
the same objects to preserve existing ``synapse_channel.relay`` imports.
"""

from synapse_channel.core.relay import (
    LITE_KEYS,
    LITE_VERSION,
    append_jsonl,
    decode_lite,
    encode_lite,
    load_offset,
    normalize_core_command,
    read_jsonl_since,
    save_offset,
    trim_jsonl_tail,
)

__all__ = (
    "LITE_KEYS",
    "LITE_VERSION",
    "append_jsonl",
    "decode_lite",
    "encode_lite",
    "load_offset",
    "normalize_core_command",
    "read_jsonl_since",
    "save_offset",
    "trim_jsonl_tail",
)
