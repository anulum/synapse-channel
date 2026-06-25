# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shell integration for automatic terminal arming
"""Render and install shell hooks that keep terminals connected to Synapse."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

DEFAULT_PROVIDER_COMMANDS = ("codex", "claude", "gemini", "agent", "ask", "ollama")
"""Provider command names wrapped by the generated shell hook by default."""

START_MARKER = "# >>> synapse-channel shell integration >>>"
END_MARKER = "# <<< synapse-channel shell integration <<<"
SUPPORTED_SHELLS = frozenset({"bash", "zsh"})


def normalise_shell(shell: str, *, env_shell: str | None = None) -> str:
    """Return a supported shell name.

    Parameters
    ----------
    shell : str
        Requested shell name or ``"auto"``.
    env_shell : str or None, optional
        Shell path used when ``shell`` is ``"auto"``. Defaults to ``$SHELL``.

    Returns
    -------
    str
        ``"bash"`` or ``"zsh"``.

    Raises
    ------
    ValueError
        If the shell cannot be mapped to a supported startup file.
    """
    requested = shell.strip().lower()
    if requested == "auto":
        requested = Path(env_shell or os.environ.get("SHELL", "bash")).name.lower()
    if requested not in SUPPORTED_SHELLS:
        raise ValueError("shell integration supports bash and zsh")
    return requested


def shell_rc_path(shell: str, *, home: Path | None = None, env_shell: str | None = None) -> Path:
    """Return the startup file for ``shell``.

    Parameters
    ----------
    shell : str
        ``"bash"``, ``"zsh"``, or ``"auto"``.
    home : Path or None, optional
        Home directory override for tests.
    env_shell : str or None, optional
        Shell path used for ``auto`` detection.
    """
    resolved = normalise_shell(shell, env_shell=env_shell)
    root = Path.home() if home is None else home
    return root / (".zshrc" if resolved == "zsh" else ".bashrc")


def _provider_function(command: str) -> str:
    """Render one provider wrapper function."""
    name = command.strip()
    quoted = shlex.quote(name)
    return (
        f"{name}() {{\n"
        "  __synapse_auto_arm || true\n"
        f"  if command -v synapse >/dev/null 2>&1; then\n"
        '    synapse worker-session --project "$SYN_PROJECT" '
        f'--identity "$SYN_IDENTITY" -- {quoted} "$@"\n'
        "  else\n"
        f'    command {quoted} "$@"\n'
        "  fi\n"
        "}\n"
    )


def render_shell_hook(
    *,
    shell: str = "bash",
    provider_commands: tuple[str, ...] = DEFAULT_PROVIDER_COMMANDS,
) -> str:
    """Render shell code that auto-arms the current project and wraps providers.

    The hook resolves the project from the git toplevel or current directory at
    each prompt, exports ``SYN_PROJECT`` and ``SYN_IDENTITY``, and starts one
    background ``synapse arm`` sidecar per terminal identity. Provider wrappers
    route commands through ``synapse worker-session`` so Codex, Claude, Gemini,
    and local commands inherit the same identity without manual setup.
    """
    resolved = normalise_shell(shell)
    providers = "\n".join(_provider_function(command) for command in provider_commands if command)
    prompt_hook = (
        'case ";${PROMPT_COMMAND:-};" in\n'
        '  *";__synapse_auto_arm;"*) ;;\n'
        '  *) PROMPT_COMMAND="__synapse_auto_arm${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;\n'
        "esac\n"
        if resolved == "bash"
        else "autoload -Uz add-zsh-hook 2>/dev/null || true\n"
        "if command -v add-zsh-hook >/dev/null 2>&1; then\n"
        "  add-zsh-hook precmd __synapse_auto_arm 2>/dev/null || true\n"
        "fi\n"
    )
    return f"""# Synapse Channel shell integration. Set SYNAPSE_AUTO_CONNECT=0 to disable.
__synapse_project() {{
  local top
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || top="$PWD"
  basename "$top"
}}

__synapse_safe_key() {{
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}}

__synapse_auto_arm() {{
  [ "${{SYNAPSE_AUTO_CONNECT:-1}}" = "0" ] && return 0
  command -v synapse >/dev/null 2>&1 || return 0
  local cwd_project project identity terminal_id runtime key pidfile logfile pid
  cwd_project="$(__synapse_project)" || return 0
  [ -n "$cwd_project" ] || return 0

  if [ -n "${{SYN_PROJECT:-}}" ] && [ "${{__SYNAPSE_AUTO_PROJECT:-}}" != "$SYN_PROJECT" ]; then
    project="$SYN_PROJECT"
  else
    project="$cwd_project"
    export SYN_PROJECT="$project"
    export __SYNAPSE_AUTO_PROJECT="$project"
  fi

  terminal_id="${{SYN_AGENT_ID:-${{SYNAPSE_TERMINAL_ID:-$$}}}}"
  if [ -n "${{SYN_IDENTITY:-}}" ] && [ "${{__SYNAPSE_AUTO_IDENTITY:-}}" != "$SYN_IDENTITY" ]; then
    identity="$SYN_IDENTITY"
  else
    identity="$project/${{SYN_AGENT_TYPE:-terminal}}-$terminal_id"
    export SYN_IDENTITY="$identity"
    export __SYNAPSE_AUTO_IDENTITY="$identity"
  fi

  runtime="${{XDG_RUNTIME_DIR:-/tmp}}/synapse-shell"
  mkdir -p "$runtime" 2>/dev/null || return 0
  key="$(__synapse_safe_key "$identity")"
  pidfile="$runtime/$key.pid"
  logfile="$runtime/$key.log"
  if [ -r "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  nohup synapse arm --name "$identity-rx" --for "$project" --directed-only \
    >"$logfile" 2>&1 &
  printf '%s\n' "$!" >"$pidfile"
}}

__synapse_run_provider() {{
  local command_name="$1"
  shift
  __synapse_auto_arm || true
  if command -v synapse >/dev/null 2>&1; then
    synapse worker-session --project "$SYN_PROJECT" --identity "$SYN_IDENTITY" -- \
      "$command_name" "$@"
  else
    command "$command_name" "$@"
  fi
}}

{providers}{prompt_hook}__synapse_auto_arm || true
"""


def render_rc_block(*, shell: str, synapse_bin: str = "synapse") -> str:
    """Render the idempotent startup-file block that loads the live hook."""
    resolved = normalise_shell(shell)
    return (
        f"{START_MARKER}\n"
        f"if command -v {shlex.quote(synapse_bin)} >/dev/null 2>&1; then\n"
        f'  eval "$({shlex.quote(synapse_bin)} shell-hook --shell {resolved})"\n'
        "fi\n"
        f"{END_MARKER}\n"
    )


def install_shell_hook(
    *,
    shell: str = "auto",
    synapse_bin: str = "synapse",
    home: Path | None = None,
    env_shell: str | None = None,
) -> list[str]:
    """Install the shell startup block if it is not already present."""
    resolved = normalise_shell(shell, env_shell=env_shell)
    path = shell_rc_path(resolved, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if START_MARKER in existing and END_MARKER in existing:
        return [f"already installed in {path}"]
    block = render_rc_block(shell=resolved, synapse_bin=synapse_bin)
    separator = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{separator}{block}", encoding="utf-8")
    return [f"installed shell hook in {path}", "open a new terminal or source the file"]
