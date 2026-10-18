# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native foreground and typed attachment wake boundaries
"""Exercise real foreground work and native file mentions before wake admission."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fixtures.opencode.composer import native_composer
from fixtures.opencode.llm import ScriptedLlmServer
from synapse_channel.agent_tmux import inject_wake, registry_path, status
from synapse_channel.opencode_composer_probe import footer_is_probeable, profile_directory


def test_unreleased_foreground_provider_response_has_a_finite_deadline() -> None:
    """A failed release cannot leave the real HTTP fixture or its teardown hanging."""
    with ScriptedLlmServer() as provider:
        provider.enqueue_text("must not be emitted", release=threading.Event())
        request = urllib.request.Request(
            provider.url + "/chat/completions",
            data=json.dumps({"messages": [{"role": "user", "content": "held request"}]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=15)
        assert refused.value.code == 504
        assert len(provider.prompt_requests) == 1


def test_busy_native_turn_defers_and_coalesces_wakes_until_completion(tmp_path: Path) -> None:
    """A real outstanding model request receives no editor or submission keys."""
    with native_composer(tmp_path) as native:
        native.seed()
        release = threading.Event()
        native.provider.enqueue_text("OWNED_FOREGROUND_COMPLETED", release=release)
        native.text("Reply OWNED_FOREGROUND_COMPLETED without tools.")
        native.keys("Enter")
        try:
            native.wait(
                lambda: len(native.provider.prompt_requests) == 2 and "interrupt" in native.screen()
            )
            request = profile_directory(native.config) / "request.json"
            original = request.read_bytes()
            assert status(native.config).pane_state == "blocked"
            for _ in range(2):
                deferred = inject_wake(native.config)
                assert deferred.returncode == 0 and not deferred.injected, deferred.detail
                assert request.read_bytes() == original
                assert len(native.provider.prompt_requests) == 2
                assert json.loads(registry_path(native.config).read_text())["pending_wake"]
        finally:
            release.set()
        native.wait(
            lambda: (
                "OWNED_FOREGROUND_COMPLETED" in native.screen()
                and "interrupt" not in native.screen()
            )
        )
        native.provider.enqueue_text("COALESCED_BUSY_WAKE_COMPLETED")
        delivered = inject_wake(native.config)
        assert delivered.returncode == 0 and delivered.injected, delivered.detail
        native.wait_requests(3)
        record = json.loads(registry_path(native.config).read_text())
        assert not record["pending_wake"] and not record["wake_prompt_staged"]


def test_file_picker_and_typed_attachment_survive_a_refused_wake(tmp_path: Path) -> None:
    """A native file part stays bound through refusal and the owner's explicit submit."""
    with native_composer(tmp_path) as native:
        content = "NATIVE_OWNED_FILE_CONTENT_qualification\n"
        attachment = native.config.cwd / "owned-note.txt"
        attachment.write_text(content)
        native.seed()
        request = profile_directory(native.config) / "request.json"
        original = request.read_bytes()
        native.text("@owned-note")
        native.wait(lambda: "owned-note.txt" in native.screen())
        picker = inject_wake(native.config)
        assert picker.returncode == 0 and not picker.injected, picker.detail
        assert request.read_bytes() == original, native.screen()
        assert len(native.provider.prompt_requests) == 1
        native.keys("Enter")
        native.wait(lambda: "@owned-note.txt" in native.screen())
        native.text("Reply FILE_ATTACHMENT_COMPLETED without tools.")
        native.wait(lambda: footer_is_probeable(native.screen()))
        before = native.readback()
        refused = inject_wake(native.config)
        assert refused.returncode == 0 and not refused.injected, refused.detail
        assert native.readback() == before
        assert len(native.provider.prompt_requests) == 1
        native.provider.enqueue_text("FILE_ATTACHMENT_COMPLETED")
        native.keys("Enter")
        native.wait_requests(2)
        database = tmp_path / "home/.local/share/opencode/opencode.db"
        assert database.is_file()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            parts = [json.loads(row[0]) for row in connection.execute("SELECT data FROM part")]
        files = [part for part in parts if part.get("type") == "file"]
        assert len(files) == 1, parts
        assert files[0]["url"] == attachment.as_uri()
        assert files[0]["source"]["text"]["value"] == "@owned-note.txt"
        requests = list(native.provider.prompt_requests)
        assert len(requests) == 2
        assert content.strip() in json.dumps(requests[-1])
