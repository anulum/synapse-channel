# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi RPC stream edges: progress records ignored, missing usage refused
"""A real pi-shaped child: streaming progress is skipped; a turn without usage fails closed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from synapse_channel.participants.pi_process import PiRpcProcess
from synapse_channel.participants.pi_rpc import PiRpcError

_PROGRESS_THEN_ANSWER = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    print(json.dumps({'type':'response','id':request['id'],
                      'command':request['type'],'success':True}),flush=True)
    if request['type']=='prompt':
        for kind in ('agent_start','message_update','tool_execution_start'):
            print(json.dumps({'type':kind,'detail':'progress'}),flush=True)
        print(json.dumps({'type':'message_end','message':{'role':'assistant',
            'stopReason':'stop','content':[{'type':'text','text':'after progress'}],
            'usage':{'input':3,'output':1,'cost':{'total':0}}}}),flush=True)
        print(json.dumps({'type':'turn_end','message':{},'toolResults':[]}),flush=True)
"""

_ANSWER_WITHOUT_USAGE = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    print(json.dumps({'type':'response','id':request['id'],
                      'command':request['type'],'success':True}),flush=True)
    if request['type']=='prompt':
        print(json.dumps({'type':'message_end','message':{'role':'assistant',
            'stopReason':'stop','content':[{'type':'text','text':'unmetered'}]}}),flush=True)
        print(json.dumps({'type':'turn_end','message':{},'toolResults':[]}),flush=True)
"""


def _child(script: str, tmp_path: Path) -> PiRpcProcess:
    return PiRpcProcess(
        [sys.executable, "-u", "-c", script], cwd=tmp_path, environment=os.environ, timeout=5
    )


async def test_streaming_progress_records_do_not_end_or_alter_a_turn(tmp_path: Path) -> None:
    """Records outside the settlement set are skipped; the turn carries only its answer."""
    async with _child(_PROGRESS_THEN_ANSWER, tmp_path) as child:
        await child.command("prompt", message="Question")
        turn = await child.next_turn()
        assert (turn.answer, turn.is_error) == ("after progress", False)
        assert (turn.input_tokens, turn.output_tokens, turn.cost_usd) == (3, 1, 0)


async def test_an_assistant_message_without_usage_is_refused(tmp_path: Path) -> None:
    """A turn whose provider message carries no token or cost usage fails closed."""
    async with _child(_ANSWER_WITHOUT_USAGE, tmp_path) as child:
        await child.command("prompt", message="Question")
        with pytest.raises(PiRpcError, match="^pi assistant message lacks final usage$"):
            await child.next_turn()
