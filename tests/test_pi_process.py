# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi RPC child ownership tests
"""Exercise the real subprocess boundary and refusal paths without a paid host."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from synapse_channel.participants.pi_process import PiRpcProcess
from synapse_channel.participants.pi_rpc import PiRpcError

_RESPONDER = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    print(json.dumps({'type':'response','id':request['id'],
                      'command':request['type'],'success':True}),flush=True)
    if request['type']=='prompt':
        print(json.dumps({'type':'message_end','message':{'role':'assistant',
            'stopReason':'stop','content':[{'type':'text','text':'settled answer'}],
            'usage':{'input':4,'output':2,'cost':{'total':0}}}}),flush=True)
        print(json.dumps({'type':'turn_end','message':{},'toolResults':[]}),flush=True)
        print(json.dumps({'type':'agent_end','messages':[],'willRetry':False}),flush=True)
"""

_FOLLOW_UP_RESPONDER = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    kind=request['type']
    print(json.dumps({'type':'response','id':request['id'],
                      'command':kind,'success':True}),flush=True)
    if kind in ('prompt','follow_up'):
        print(json.dumps({'type':'message_end','message':{'role':'assistant',
            'stopReason':'aborted' if kind=='abort' else 'stop',
            'content':[{'type':'text','text':kind}],
            'usage':{'input':4,'output':2,'cost':{'total':0}}}}),flush=True)
        print(json.dumps({'type':'turn_end','message':{},'toolResults':[]}),flush=True)
    if kind=='follow_up':
        print(json.dumps({'type':'agent_end','messages':[],'willRetry':False}),flush=True)
"""

_TOOL_RESPONDER = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    print(json.dumps({'type':'response','id':request['id'],
                      'command':request['type'],'success':True}),flush=True)
    if request['type']=='prompt':
        for reason,text,results in (
            ('toolUse','','blocked'),('stop','write was refused',None)):
            print(json.dumps({'type':'message_end','message':{'role':'assistant',
                'stopReason':reason,'content':[{'type':'text','text':text}],
                'usage':{'input':4,'output':2,'cost':{'total':0}}}}),flush=True)
            print(json.dumps({'type':'turn_end','message':{},
                'toolResults':[{'isError':True}] if results else []}),flush=True)
        print(json.dumps({'type':'agent_end','messages':[],'willRetry':False}),flush=True)
        print(json.dumps({'type':'agent_settled'}),flush=True)
"""


async def test_real_child_receipt_is_separate_from_settled_turn(tmp_path: Path) -> None:
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", _RESPONDER],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        receipt = await child.command("prompt", message="A read-only question")
        assert receipt["success"] is True
        result = await child.next_turn()
        assert result.answer == "settled answer"
        assert result.is_error is False
        assert (result.input_tokens, result.output_tokens, result.cost_usd) == (4, 2, 0)


async def test_follow_up_settles_each_turn_before_agent_end(tmp_path: Path) -> None:
    """A queued follow-up has its own turn_end before the session-level agent_end."""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", _FOLLOW_UP_RESPONDER],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        await child.command("prompt", message="First")
        await child.command("follow_up", message="Second")
        first = await child.next_turn()
        second = await child.next_turn()
        assert (first.answer, second.answer) == ("prompt", "follow_up")
        assert not first.is_error and not second.is_error


async def test_full_run_waits_past_tool_turn_and_marks_refusal(tmp_path: Path) -> None:
    """Tool failure remains visible after a later assistant answer settles."""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", _TOOL_RESPONDER],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        await child.command("prompt", message="Try a file write")
        result = await child.next_settled()
        assert result.answer == "write was refused"
        assert result.is_error
        assert result.tool_error
        assert result.input_tokens == 8


async def test_reserved_rpc_fields_cannot_replace_correlation(tmp_path: Path) -> None:
    """Caller-supplied IDs cannot override the generated response correlation ID."""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", _RESPONDER],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        with pytest.raises(PiRpcError, match="reserved"):
            await child.command("prompt", id="forged")


async def test_wrong_child_response_id_fails_closed(tmp_path: Path) -> None:
    wrong = (
        "import json,sys; json.loads(sys.stdin.readline()); "
        "print(json.dumps({'type':'response','id':'wrong','command':'prompt','success':True}),"
        "flush=True); sys.stdin.read()"
    )
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", wrong],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        with pytest.raises(PiRpcError, match="unknown response id"):
            await child.command("prompt", message="A question")


async def test_exited_child_cannot_leave_command_waiting(tmp_path: Path) -> None:
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", "import sys;sys.stdin.readline()"],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        with pytest.raises(PiRpcError, match="exited before session close"):
            await child.command("prompt", message="A question")


def test_process_configuration_rejects_invalid_argv_and_timeout(tmp_path: Path) -> None:
    """A missing executable or unbounded timeout cannot create a child."""
    with pytest.raises(ValueError, match="argv"):
        PiRpcProcess([], cwd=tmp_path, environment=os.environ)
    with pytest.raises(ValueError, match="timeout"):
        PiRpcProcess([sys.executable], cwd=tmp_path, environment=os.environ, timeout=float("nan"))


async def test_command_limits_and_refused_receipt(tmp_path: Path) -> None:
    """Oversized input and an explicit host refusal never become accepted work."""
    child = PiRpcProcess([sys.executable], cwd=tmp_path, environment=os.environ)
    with pytest.raises(PiRpcError, match="unavailable"):
        await child.command("prompt", message="test")

    responder = r"""
import json,sys
for row in sys.stdin.buffer:
    request=json.loads(row)
    print(json.dumps({'type':'response','id':request['id'],
                      'command':request['type'],'success':False}),flush=True)
"""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", responder],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as running:
        with pytest.raises(PiRpcError, match="byte limit"):
            await running.command("prompt", message="x" * 70_000)
        with pytest.raises(PiRpcError, match="reserved"):
            await running.command("", message="test")
        with pytest.raises(PiRpcError, match="refused"):
            await running.command("prompt", message="test")


async def test_close_kills_descendant_after_rpc_leader_exits(tmp_path: Path) -> None:
    """An exited pi leader cannot strand a process in its owned process group."""
    pid_file = tmp_path / "descendant.pid"
    parent = r"""
import os,subprocess,sys
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
pending=sys.argv[1]+'.pending'
with open(pending,'w',encoding='ascii') as target:target.write(str(child.pid))
os.replace(pending,sys.argv[1])
"""
    running = PiRpcProcess(
        [sys.executable, "-u", "-c", parent, str(pid_file)],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    )
    await running.start()
    try:
        for _ in range(50):
            if pid_file.exists():
                break
            await asyncio.sleep(0.02)
        assert pid_file.exists()
        descendant = int(pid_file.read_text(encoding="ascii"))
        await asyncio.sleep(0.1)
        await running.close()
        for _ in range(50):
            status = Path(f"/proc/{descendant}/status")
            if not status.exists() or "State:\tZ" in status.read_text(encoding="utf-8"):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("pi process group descendant remained runnable")
    finally:
        await running.close()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text(encoding="ascii")), signal.SIGKILL)
            except ProcessLookupError:
                pass


async def test_receipt_timeout_closes_unresponsive_child(tmp_path: Path) -> None:
    """No command receipt means the owned host cannot remain alive indefinitely."""
    sleeper = "import sys,time;sys.stdin.readline();time.sleep(30)"
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", sleeper],
        cwd=tmp_path,
        environment=os.environ,
        timeout=0.5,
    ) as child:
        with pytest.raises(PiRpcError, match="response timed out"):
            await child.command("prompt", message="Question")
        with pytest.raises(PiRpcError, match="unavailable"):
            await child.command("get_state")


async def test_missing_settlement_times_out_and_closes_child(tmp_path: Path) -> None:
    """An accepted prompt without agent_settled is an error, not an answer."""
    responder = r"""
import json,sys,time
request=json.loads(sys.stdin.readline())
print(json.dumps({'type':'response','id':request['id'],
                  'command':request['type'],'success':True}),flush=True)
time.sleep(30)
"""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", responder],
        cwd=tmp_path,
        environment=os.environ,
        timeout=0.5,
    ) as child:
        await child.command("prompt", message="Question")
        with pytest.raises(PiRpcError, match="did not settle"):
            await child.next_settled()


async def test_cancelled_command_reaps_its_owned_child(tmp_path: Path) -> None:
    """Caller cancellation cannot leave a pending pi process behind."""
    sleeper = "import sys,time;sys.stdin.readline();time.sleep(30)"
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", sleeper],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        pending = asyncio.create_task(child.command("prompt", message="Question"))
        await asyncio.sleep(0.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(PiRpcError, match="unavailable"):
            await child.command("get_state")


async def test_malformed_settlement_events_are_refused(tmp_path: Path) -> None:
    """The host cannot omit tool outcomes or retry status in a settled run."""
    for broken in (
        "{'type':'turn_end','message':{}}",
        "{'type':'agent_end','messages':[]}",
    ):
        responder = f"""
import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({{'type':'response','id':request['id'],
                  'command':request['type'],'success':True}}),flush=True)
print(json.dumps({broken}),flush=True)
sys.stdin.read()
"""
        async with PiRpcProcess(
            [sys.executable, "-u", "-c", responder],
            cwd=tmp_path,
            environment=os.environ,
            timeout=5,
        ) as child:
            await child.command("prompt", message="Question")
            with pytest.raises(PiRpcError, match="tool results|retry status"):
                await child.next_settled()


async def test_child_starts_once_and_drains_stderr(tmp_path: Path) -> None:
    """A noisy provider cannot block RPC, and starting twice cannot orphan a child."""
    noisy = _RESPONDER.replace("import json,sys", "import json,sys\nsys.stderr.write('x'*200000)")
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", noisy],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        with pytest.raises(PiRpcError, match="already started"):
            await child.start()
        await child.command("prompt", message="Question")
        assert (await child.next_turn()).answer == "settled answer"


async def test_child_exit_while_waiting_for_settlement(tmp_path: Path) -> None:
    """A receipt cannot turn a crashed model process into a completed turn."""
    responder = r"""
import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({'type':'response','id':request['id'],
                  'command':request['type'],'success':True}),flush=True)
"""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", responder],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        await child.command("prompt", message="Question")
        with pytest.raises(PiRpcError, match="exited before session close"):
            await child.next_settled()


async def test_close_escalates_for_child_ignoring_termination(tmp_path: Path) -> None:
    """An uncooperative provider is killed after the bounded graceful shutdown."""
    stubborn = r"""
import json,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
request=json.loads(sys.stdin.readline())
print(json.dumps({'type':'response','id':request['id'],
                  'command':request['type'],'success':True}),flush=True)
time.sleep(30)
"""
    child = PiRpcProcess(
        [sys.executable, "-u", "-c", stubborn],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    )
    await child.start()
    try:
        await child.command("get_state")
        await asyncio.wait_for(child.close(), timeout=5)
        with pytest.raises(PiRpcError, match="unavailable"):
            await child.command("get_state")
    finally:
        await child.close()


async def test_child_closing_stdin_refuses_new_rpc_command(tmp_path: Path) -> None:
    """A still-running provider with a broken request pipe cannot accept work."""
    marker = tmp_path / "stdin-closed"
    responder = r"""
import json,os,sys,time
request=json.loads(sys.stdin.readline())
print(json.dumps({'type':'response','id':request['id'],
                  'command':request['type'],'success':True}),flush=True)
os.close(0)
open(sys.argv[1],'w',encoding='ascii').close()
time.sleep(30)
"""
    async with PiRpcProcess(
        [sys.executable, "-u", "-c", responder, str(marker)],
        cwd=tmp_path,
        environment=os.environ,
        timeout=5,
    ) as child:
        await child.command("get_state")
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists()
        with pytest.raises(PiRpcError, match="could not reach the child"):
            await child.command("prompt", message="Do not run")
