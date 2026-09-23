# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi participant real subprocess tests
"""Exercise pinned pi CLI, owner-private sessions and typed turn results."""

from __future__ import annotations

import shutil
import stat
import uuid
from pathlib import Path

import pytest

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_pi import (
    PiClaimBinding,
    PiParticipant,
    _private_session_dir,
    _require_resume_file,
)

_FAKE_PI = r"""#!/usr/bin/env python3
import json,os,sys
if '--version' in sys.argv:
    print('0.87.1')
    raise SystemExit
session=sys.argv[sys.argv.index('--session-id')+1]
session_dir=sys.argv[sys.argv.index('--session-dir')+1]
for line in sys.stdin:
    request=json.loads(line)
    kind=request['type']
    data={'sessionId':session} if kind=='get_state' else None
    if kind=='get_commands':
        extension=sys.argv[sys.argv.index('--extension')+1]
        commands=[] if os.environ.get('FAKE_PI_DROP_GUARD') else [
            {'name':'synapse-claim-guard-health','source':'extension',
             'sourceInfo':{'path':extension}}]
        data={'commands':commands}
    print(json.dumps({'type':'response','id':request['id'],
                      'command':kind,'success':True,'data':data}),flush=True)
    if kind=='prompt':
        session_file=os.path.join(session_dir,'fixture_'+session+'.jsonl')
        with open(session_file,'w',encoding='utf-8') as output:
            output.write('{}\n')
        os.chmod(session_file,0o600)
        print(json.dumps({'type':'message_end','message':{'role':'assistant',
              'stopReason':'stop','content':[{'type':'text','text':'ack'}],
              'usage':{'input':3,'output':1,'cost':{'total':0}}}}),flush=True)
        print(json.dumps({'type':'turn_end','message':{},'toolResults':[]}),flush=True)
        print(json.dumps({'type':'agent_end','messages':[],'willRetry':False}),flush=True)
        print(json.dumps({'type':'agent_settled'}),flush=True)
"""


def _binary(tmp_path: Path) -> Path:
    """Create a real child executable with the pinned RPC receipt/event shape."""
    binary = tmp_path / "pi-fixture"
    binary.write_text(_FAKE_PI, encoding="utf-8")
    binary.chmod(0o700)
    return binary


async def test_turn_and_resume_use_private_same_session(tmp_path: Path) -> None:
    """Separate child processes retain the precise provider session token."""
    directory = tmp_path / "sessions"
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="ollama/local",
        binary=str(_binary(tmp_path)),
        session_dir=directory,
        timeout=5,
    )
    assert participant.health().available
    assert participant.identity == "project/pi"
    first = await participant.take_turn(TurnRequest(topic_id="topic", prompt="First"))
    second = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Second", resume_session=first["session"])
    )
    assert first["is_error"] is False
    assert second["is_error"] is False
    assert first["session"] == second["session"]
    assert (first["input_tokens"], first["output_tokens"]) == (3, 1)
    assert second["answer"] == "ack"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


async def test_invalid_resume_and_public_session_dir_fail_closed(tmp_path: Path) -> None:
    """A path-like resume token or group-visible sessions cannot drive pi."""
    directory = tmp_path / "sessions"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="ollama/local",
        binary=str(_binary(tmp_path)),
        session_dir=directory,
        timeout=5,
    )
    invalid = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Question", resume_session="../../foreign")
    )
    exposed = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert invalid["is_error"] and "exact UUID" in invalid["reason"]
    assert exposed["is_error"] and "must be private" in exposed["reason"]


async def test_unknown_resume_id_does_not_create_a_new_session(tmp_path: Path) -> None:
    """Pi's create-if-missing host behavior is not mistaken for a resumed turn."""
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="ollama/local",
        binary=str(_binary(tmp_path)),
        session_dir=tmp_path / "sessions",
        timeout=5,
    )
    result = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Question", resume_session=str(uuid.uuid4()))
    )
    assert result["is_error"] and "missing or ambiguous" in result["reason"]


async def test_guarded_turn_requires_exact_loaded_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension load failure stops before a tool-enabled prompt is sent."""
    extension = tmp_path / "guard.ts"
    extension.write_text("export default function() {}", encoding="utf-8")
    binding = PiClaimBinding(
        project="PROJECT",
        repository=tmp_path,
        task_id="TASK-1",
        epoch=7,
        uri="ws://unused",
        extension=extension,
    )
    directory = tmp_path / "sessions"
    participant = PiParticipant(
        "PROJECT/seat",
        directory=tmp_path,
        model="ollama/local",
        binary=str(_binary(tmp_path)),
        session_dir=directory,
        claim_binding=binding,
        timeout=5,
    )
    success = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert not success["is_error"]
    before = len(tuple(directory.glob("*.jsonl")))
    monkeypatch.setenv("FAKE_PI_DROP_GUARD", "1")
    refused = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert refused["is_error"] and "extension did not load" in refused["reason"]
    assert len(tuple(directory.glob("*.jsonl"))) == before


async def test_missing_model_binary_and_bad_version_refuse_turn(tmp_path: Path) -> None:
    """Provider readiness and explicit model selection are checked before RPC."""
    missing = PiParticipant("project/pi", directory=tmp_path, binary="missing-pi-binary")
    assert not missing.health().available
    request = TurnRequest(topic_id="topic", prompt="Question")
    assert (await missing.take_turn(request))["is_error"]

    binary = _binary(tmp_path)
    no_model = PiParticipant("project/pi", directory=tmp_path, binary=str(binary))
    result = await no_model.take_turn(request)
    assert result["is_error"] and "model must be explicit" in result["reason"]

    binary.write_text(_FAKE_PI.replace("print('0.87.1')", "print('0.87.2')"), encoding="utf-8")
    wrong = PiParticipant("project/pi", directory=tmp_path, model="local", binary=str(binary))
    assert not wrong.health().available
    assert "not verified" in (await wrong.take_turn(request))["reason"]
    with pytest.raises(ValueError, match="timeout"):
        PiParticipant("project/pi", directory=tmp_path, timeout=float("inf"))


async def test_pi_binary_disappearing_after_health_refuses_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removed binary between the version probe and RPC cannot start a turn."""
    binary = _binary(tmp_path)
    participant = PiParticipant(
        "project/pi", directory=tmp_path, model="ollama/local", binary=str(binary)
    )
    real_which = shutil.which
    calls = 0

    def disappearing_which(command: str) -> str | None:
        nonlocal calls
        calls += 1
        return real_which(command) if calls == 1 else None

    monkeypatch.setattr(shutil, "which", disappearing_which)
    result = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert result["is_error"]
    assert result["reason"] == "pi binary disappeared after version check"
    assert calls == 2


async def test_resume_rejects_symlink_and_public_session_file(tmp_path: Path) -> None:
    """Provider session continuity cannot follow a link or a group-visible file."""
    directory = tmp_path / "sessions"
    directory.mkdir(mode=0o700)
    external = tmp_path / "external.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    token = str(uuid.uuid4())
    alias = directory / f"fixture_{token}.jsonl"
    alias.symlink_to(external)
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="local",
        binary=str(_binary(tmp_path)),
        session_dir=directory,
        timeout=5,
    )
    denied = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Question", resume_session=token)
    )
    assert denied["is_error"] and "missing or ambiguous" in denied["reason"]
    alias.unlink()
    alias.write_text("{}\n", encoding="utf-8")
    alias.chmod(0o644)
    public = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Question", resume_session=token)
    )
    assert public["is_error"] and "must be private" in public["reason"]


async def test_guarded_turn_refuses_writable_extension_and_wrong_session(
    tmp_path: Path,
) -> None:
    """The exact local guard file and provider session must be trustworthy."""
    extension = tmp_path / "guard.ts"
    extension.write_text("export default function() {}", encoding="utf-8")
    extension.chmod(0o666)
    binary = _binary(tmp_path)
    binding = PiClaimBinding(
        project="PROJECT",
        repository=tmp_path,
        task_id="TASK-1",
        epoch=7,
        uri="ws://unused",
        extension=extension,
    )
    participant = PiParticipant(
        "PROJECT/seat",
        directory=tmp_path,
        model="local",
        binary=str(binary),
        session_dir=tmp_path / "sessions",
        claim_binding=binding,
        timeout=5,
    )
    denied = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert denied["is_error"] and "extension and hub URI" in denied["reason"]

    extension.chmod(0o600)
    binary.write_text(
        _FAKE_PI.replace("data={'sessionId':session}", "data={'sessionId':'other'}"),
        encoding="utf-8",
    )
    mismatch = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert mismatch["is_error"] and "session identity" in mismatch["reason"]


async def test_resume_rejects_alias_id_and_session_directory_link(tmp_path: Path) -> None:
    """Resume cannot select a second spelling or follow an alternate session tree."""
    directory = tmp_path / "sessions"
    target = tmp_path / "real-sessions"
    target.mkdir(mode=0o700)
    directory.symlink_to(target, target_is_directory=True)
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="local",
        binary=str(_binary(tmp_path)),
        session_dir=directory,
        timeout=5,
    )
    token = str(uuid.uuid4())
    aliased = await participant.take_turn(
        TurnRequest(topic_id="topic", prompt="Question", resume_session=token.upper())
    )
    linked = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert aliased["is_error"] and "canonical UUID" in aliased["reason"]
    assert linked["is_error"] and "symlink" in linked["reason"]
    assert not tuple(target.iterdir())


async def test_guarded_turn_passes_exact_token_reference(tmp_path: Path) -> None:
    """A private token file is referenced for the hook without exposing its contents."""
    repository = tmp_path / "repo"
    repository.mkdir()
    extension = tmp_path / "guard.ts"
    extension.write_text("export default function() {}", encoding="utf-8")
    token = tmp_path / "hub.token"
    token.write_text("fixture-secret", encoding="utf-8")
    token.chmod(0o600)
    binary = _binary(tmp_path)
    binary.write_text(
        _FAKE_PI.replace(
            "session=sys.argv[sys.argv.index('--session-id')+1]",
            "assert os.environ.get('SYNAPSE_PI_TOKEN_FILE') == "
            + repr(str(token))
            + "\nsession=sys.argv[sys.argv.index('--session-id')+1]",
        ),
        encoding="utf-8",
    )
    binding = PiClaimBinding(
        project="PROJECT",
        repository=repository,
        task_id="TASK-1",
        epoch=7,
        uri="ws://unused",
        extension=extension,
        token_file=token,
    )
    participant = PiParticipant(
        "PROJECT/seat",
        directory=repository,
        model="local",
        binary=str(binary),
        session_dir=tmp_path / "sessions",
        claim_binding=binding,
        timeout=5,
    )
    result = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert result["is_error"] is False

    inside = repository / "token"
    inside.write_text("private", encoding="utf-8")
    inside.chmod(0o600)
    unsafe = PiParticipant(
        "PROJECT/seat",
        directory=repository,
        model="local",
        binary=str(binary),
        session_dir=tmp_path / "sessions",
        claim_binding=PiClaimBinding(
            project="PROJECT",
            repository=repository,
            task_id="TASK-1",
            epoch=7,
            uri="ws://unused",
            extension=extension,
            token_file=inside,
        ),
        timeout=5,
    )
    refused = await unsafe.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
    assert refused["is_error"] and "outside the repository" in refused["reason"]


async def test_guarded_turn_rejects_public_or_nonfile_hub_token(tmp_path: Path) -> None:
    """A model session cannot launch with a readable or non-regular hub secret."""
    repository = tmp_path / "repo"
    repository.mkdir()
    extension = tmp_path / "guard.ts"
    extension.write_text("export default function() {}", encoding="utf-8")
    token = tmp_path / "hub.token"
    token.write_text("secret", encoding="utf-8")
    token.chmod(0o644)
    binary = _binary(tmp_path)
    for candidate in (token, tmp_path):
        participant = PiParticipant(
            "PROJECT/seat",
            directory=repository,
            model="local",
            binary=str(binary),
            session_dir=tmp_path / "sessions",
            claim_binding=PiClaimBinding(
                project="PROJECT",
                repository=repository,
                task_id="TASK-1",
                epoch=7,
                uri="ws://unused",
                extension=extension,
                token_file=candidate,
            ),
            timeout=5,
        )
        denied = await participant.take_turn(TurnRequest(topic_id="topic", prompt="Question"))
        assert denied["is_error"] and "owner-private" in denied["reason"]


def test_foreign_owned_session_storage_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session tree or transcript from another UID cannot be reused."""
    directory = tmp_path / "sessions"
    directory.mkdir(mode=0o700)
    token = str(uuid.uuid4())
    transcript = directory / f"fixture_{token}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    transcript.chmod(0o600)
    real_uid = directory.stat().st_uid
    monkeypatch.setattr("synapse_channel.participants.headless_pi.os.getuid", lambda: real_uid + 1)
    with pytest.raises(ValueError, match="owned by the current user"):
        _private_session_dir(directory)
    with pytest.raises(ValueError, match="owner-owned file"):
        _require_resume_file(directory, token)


def test_unresponsive_pi_version_check_is_unavailable(tmp_path: Path) -> None:
    """A hanging host cannot make the participant appear healthy."""
    binary = tmp_path / "hung-pi"
    binary.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    binary.chmod(0o700)
    participant = PiParticipant(
        "project/pi",
        directory=tmp_path,
        model="local",
        binary=str(binary),
        timeout=0.2,
    )
    assert not participant.health().available
