# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pre-mutation refusal boundaries
"""Check actual parser and session failures without mislabelling asynchronous work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from protected_write_control_examples import control_authority
from synapse_channel.core.errors import error_code
from synapse_channel.core.protected_write_refusal import (
    ProtectedWriteRefusalError,
    protected_write_pre_commit,
)
from synapse_channel.core.protected_write_request import (
    ProtectedWriteRequestError,
    parse_protected_write_request,
)
from test_protected_write_proposal import LIMITS
from test_protected_write_request import _request


def test_pre_mutation_adapter_keeps_actual_parser_results_and_marks_only_refusals() -> None:
    parse = protected_write_pre_commit(parse_protected_write_request)
    raw = json.dumps(_request("status"))
    assert parse(raw, limits=LIMITS) == parse_protected_write_request(raw, limits=LIMITS)
    with pytest.raises(ProtectedWriteRefusalError) as error:
        parse('{"type":"claim"}', limits=LIMITS)
    assert isinstance(error.value.__cause__, ProtectedWriteRequestError)
    assert str(error.value) == str(error.value.__cause__)
    assert error_code(error.value) == "protected_write_refusal"
    nested = protected_write_pre_commit(parse)
    with pytest.raises(ProtectedWriteRefusalError) as repeated:
        nested('{"type":"claim"}', limits=LIMITS)
    assert isinstance(repeated.value.__cause__, ProtectedWriteRequestError)


async def test_pre_mutation_adapter_does_not_label_real_async_session_failure(
    tmp_path: Path,
) -> None:
    async with control_authority(tmp_path) as authority:
        before = authority.store.read_all()
        exchange = protected_write_pre_commit(authority.ingress.open_session().exchange)
        with pytest.raises(ValueError) as error:
            await exchange('{"type":"claim"}')
        assert not isinstance(error.value, ProtectedWriteRefusalError)
        assert authority.store.read_all() == before
