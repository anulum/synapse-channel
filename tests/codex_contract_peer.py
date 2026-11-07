# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — contract host for Codex package custody tests
"""Speak only the host commands the Codex package custody code invokes.

This is not a Codex qualification. Tests that require the reviewed Codex
binary stay skipped when that binary is absent.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _version(mode: str) -> None:
    """Print the version probe, or fail it on purpose."""
    if mode == "hang":
        time.sleep(30)
    if mode == "flip-version":
        counter = Path(__file__).with_suffix(".count")
        seen = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
        counter.write_text(str(seen + 1), encoding="utf-8")
        print("codex-cli 0.0.0" if seen else "codex-cli 0.156.0")
        return
    if mode == "bad-version":
        print("not-codex")
        return
    if mode == "version-status":
        raise SystemExit(3)
    print("codex-cli 0.156.0")


def _quote(value: str) -> str:
    """Return one TOML basic string."""
    return json.dumps(value)


def _recorded(home: Path) -> tuple[str, list[str]]:
    """Return the command recorded when the entry was added."""
    side = home / ".peer-entry.json"
    if not side.exists():
        return "", []
    raw = json.loads(side.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return "", []
    command = raw.get("command")
    cmd_args = raw.get("args")
    if not isinstance(command, str) or not isinstance(cmd_args, list):
        return "", []
    if not all(isinstance(item, str) for item in cmd_args):
        return "", []
    return command, [item for item in cmd_args if isinstance(item, str)]


def _write_entry(config: Path, command: str, cmd_args: list[str], env: dict[str, str]) -> None:
    """Append the synapse table and remember the command for a later read."""
    previous = config.read_text(encoding="utf-8") if config.exists() else ""
    if previous:
        config.with_name(".peer-before").write_text(previous, encoding="utf-8")
    lines = [previous.rstrip()] if previous else []
    lines.append("[mcp_servers.synapse]")
    lines.append(f"command = {_quote(command)}")
    rendered = ", ".join(_quote(item) for item in cmd_args)
    lines.append(f"args = [{rendered}]")
    lines.append("[mcp_servers.synapse.env]")
    for key, value in env.items():
        lines.append(f"{key} = {_quote(value)}")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config.with_name(".peer-entry.json").write_text(
        json.dumps({"command": command, "args": cmd_args}),
        encoding="utf-8",
    )


def _parse_add(args: list[str]) -> tuple[str, list[str], dict[str, str]]:
    """Read ``mcp add --env KEY=VALUE synapse -- command args``."""
    env: dict[str, str] = {}
    command = ""
    cmd_args: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--":
            if index + 1 >= len(args):
                raise SystemExit(2)
            command = args[index + 1]
            cmd_args = args[index + 2 :]
            break
        if item == "--env":
            if index + 1 >= len(args):
                raise SystemExit(2)
            key, value = args[index + 1].split("=", 1)
            env[key] = value
            index += 2
            continue
        index += 1
    if not command:
        raise SystemExit(2)
    return command, cmd_args, env


def _add(home: Path, args: list[str], mode: str) -> None:
    """Write the entry the package reads back, or refuse."""
    if mode == "add-fail":
        raise SystemExit(7)
    command, cmd_args, env = _parse_add(args)
    if mode == "add-silent":
        return
    if mode == "add-no-owner":
        env = {}
    _write_entry(home / "config.toml", command, cmd_args, env)


def _get(home: Path, mode: str) -> None:
    """Print validator JSON, including the shapes the package refuses."""
    if mode == "get-fail":
        raise SystemExit(7)
    if mode == "get-bad-json":
        print("{broken")
        return
    if mode == "get-list":
        print("[]")
        return
    command, cmd_args = _recorded(home)
    if mode == "get-drop" and (home / "config.toml").exists():
        (home / "config.toml").unlink()
    if mode == "get-http":
        print(json.dumps({"transport": {"type": "http"}}))
        return
    if mode == "get-wrong-command":
        command = "other"
    if mode == "get-wrong-args":
        cmd_args = ["nope"]
    print(json.dumps({"transport": {"type": "stdio", "command": command, "args": cmd_args}}))


def _remove(home: Path, mode: str) -> None:
    """Delete the synapse table, keep it, or fail."""
    if mode == "remove-fail":
        raise SystemExit(7)
    if mode == "remove-keep":
        return
    config = home / "config.toml"
    previous = home / ".peer-before"
    if previous.exists():
        config.write_text(previous.read_text(encoding="utf-8"), encoding="utf-8")
    elif config.exists():
        config.write_text("", encoding="utf-8")
    side = home / ".peer-entry.json"
    if side.exists():
        side.unlink()


def main() -> None:
    """Dispatch one host invocation."""
    mode = os.environ.get("SYNAPSE_CODEX_PEER", "ok")
    args = sys.argv[1:]
    if args == ["--version"]:
        _version(mode)
        return
    if mode == "hang":
        time.sleep(30)
    home = Path(os.environ["CODEX_HOME"])
    if args[:2] == ["mcp", "add"]:
        _add(home, args[2:], mode)
        return
    if args[:3] == ["mcp", "get", "synapse"] and args[3:] == ["--json"]:
        _get(home, mode)
        return
    if args == ["mcp", "remove", "synapse"]:
        _remove(home, mode)
        return
    raise SystemExit(2)


if __name__ == "__main__":
    main()
