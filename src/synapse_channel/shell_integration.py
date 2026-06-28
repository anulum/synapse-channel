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

DEFAULT_PROVIDER_COMMANDS = (
    "codex",
    "claude",
    "kimi",
    "grok",
    "gemini",
    "agent",
    "ask",
    "ollama",
)
"""Provider command names wrapped by the generated shell hook by default."""

START_MARKER = "# >>> synapse-channel shell integration >>>"
END_MARKER = "# <<< synapse-channel shell integration <<<"
SUPPORTED_SHELLS = frozenset({"bash", "fish", "zsh"})


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
        ``"bash"``, ``"fish"``, or ``"zsh"``.

    Raises
    ------
    ValueError
        If the shell cannot be mapped to a supported startup file.
    """
    requested = shell.strip().lower()
    if requested == "auto":
        requested = Path(env_shell or os.environ.get("SHELL", "bash")).name.lower()
    if requested not in SUPPORTED_SHELLS:
        raise ValueError("shell integration supports bash, fish, and zsh")
    return requested


def shell_rc_path(shell: str, *, home: Path | None = None, env_shell: str | None = None) -> Path:
    """Return the startup file for ``shell``.

    Parameters
    ----------
    shell : str
        ``"bash"``, ``"fish"``, ``"zsh"``, or ``"auto"``.
    home : Path or None, optional
        Home directory override for tests.
    env_shell : str or None, optional
        Shell path used for ``auto`` detection.
    """
    resolved = normalise_shell(shell, env_shell=env_shell)
    root = Path.home() if home is None else home
    if resolved == "zsh":
        return root / ".zshrc"
    if resolved == "fish":
        return root / ".config" / "fish" / "config.fish"
    return root / ".bashrc"


def _provider_function(command: str) -> str:
    """Render one provider wrapper function."""
    name = command.strip()
    quoted = shlex.quote(name)
    return (
        f"{name}() {{\n"
        "  __synapse_auto_arm || true\n"
        f"  if command -v synapse >/dev/null 2>&1; then\n"
        "    __synapse_release_waiter || true\n"
        '    synapse worker-session --project "$SYN_PROJECT" '
        f'--identity "$SYN_IDENTITY" -- {quoted} "$@"\n'
        "  else\n"
        f'    command {quoted} "$@"\n'
        "  fi\n"
        "}\n"
    )


def _fish_provider_function(command: str) -> str:
    """Render one Fish provider wrapper function."""
    name = command.strip()
    quoted = shlex.quote(name)
    return (
        f"function {name} --wraps {quoted}\n"
        "  __synapse_auto_arm >/dev/null 2>&1; or true\n"
        "  if command -q synapse\n"
        "    __synapse_release_waiter >/dev/null 2>&1; or true\n"
        '    synapse worker-session --project "$SYN_PROJECT" '
        f'--identity "$SYN_IDENTITY" -- {quoted} $argv\n'
        "  else\n"
        f"    command {quoted} $argv\n"
        "  end\n"
        "end\n"
    )


def _render_fish_shell_hook(provider_commands: tuple[str, ...]) -> str:
    """Render Fish shell integration code."""
    providers = "\n".join(
        _fish_provider_function(command) for command in provider_commands if command
    )
    return f"""# Synapse Channel shell integration. Set SYNAPSE_AUTO_CONNECT=0 to disable.
function __synapse_marker_project
  set -l top (git rev-parse --show-toplevel 2>/dev/null)
  test -n "$top"; or return 1
  set -l marker "$top/.synapse/project"
  test -r "$marker"; or return 1
  set -l project (string trim -- (head -n 1 "$marker" 2>/dev/null))
  if test -n "$project"
    printf '%s\\n' "$project"
  else
    basename "$top"
  end
end

function __synapse_cwd_project
  set -l top (git rev-parse --show-toplevel 2>/dev/null)
  if test -z "$top"
    set top "$PWD"
  end
  basename "$top"
end

function __synapse_project
  if test -n "$SYN_IDENTITY"; and test "$__SYNAPSE_AUTO_IDENTITY" != "$SYN_IDENTITY"
    if string match -q "*/*" "$SYN_IDENTITY"
      string split -m1 / "$SYN_IDENTITY" | head -n 1
      return 0
    end
  end
  if test "$SYNAPSE_AUTO_PROJECT_FROM_CWD" = "1"
    __synapse_cwd_project
    return $status
  end
  set -l marker_project (__synapse_marker_project)
  if test -n "$marker_project"
    printf '%s\\n' "$marker_project"
    return 0
  end
  if test -n "$SYNAPSE_DEFAULT_PROJECT"
    printf '%s\\n' "$SYNAPSE_DEFAULT_PROJECT"
  else
    printf '%s\\n' user
  end
end

function __synapse_safe_key
  printf '%s' "$argv[1]" | tr -c 'A-Za-z0-9_.-' '_'
end

function __synapse_auto_arm --on-event fish_prompt
  if test "$SYNAPSE_AUTO_CONNECT" = "0"
    return 0
  end
  command -q synapse; or return 0
  set -l project
  if test -n "$SYN_PROJECT"; and test "$__SYNAPSE_AUTO_PROJECT" != "$SYN_PROJECT"
    set project "$SYN_PROJECT"
  else
    set project (__synapse_project)
    test -n "$project"; or return 0
    set -gx SYN_PROJECT "$project"
    set -gx __SYNAPSE_AUTO_PROJECT "$project"
  end

  set -l terminal_id "$SYN_AGENT_ID"
  if test -z "$terminal_id"
    set terminal_id "$SYNAPSE_TERMINAL_ID"
  end
  if test -z "$terminal_id"
    set terminal_id (echo %self)
  end

  set -l identity
  if test -n "$SYN_IDENTITY"; and test "$__SYNAPSE_AUTO_IDENTITY" != "$SYN_IDENTITY"
    set identity "$SYN_IDENTITY"
  else
    set identity "$project/terminal-$terminal_id"
    if test -n "$SYN_AGENT_TYPE"
      set identity "$project/$SYN_AGENT_TYPE-$terminal_id"
    end
    set -gx SYN_IDENTITY "$identity"
    set -gx __SYNAPSE_AUTO_IDENTITY "$identity"
  end

  set -l runtime "$XDG_RUNTIME_DIR/synapse-shell"
  if test -z "$XDG_RUNTIME_DIR"
    set runtime "/tmp/synapse-shell"
  end
  mkdir -p "$runtime" 2>/dev/null; or return 0
  set -l key (__synapse_safe_key "$identity")
  set -l pidfile "$runtime/$key.pid"
  set -l logfile "$runtime/$key.log"
  if test -r "$pidfile"
    set -l pid (cat "$pidfile" 2>/dev/null)
    if test -n "$pid"; and kill -0 "$pid" 2>/dev/null
      return 0
    end
  end
  # Yield to an active worker-session tmux waker: it owns "$identity-rx" and can
  # actually wake the agent's pane, so a second passive waiter on the same name
  # would only collide and lock the injecting waker out.
  set -l provider_runtime "$XDG_RUNTIME_DIR/synapse-provider-tmux"
  if test -z "$XDG_RUNTIME_DIR"
    set provider_runtime "/tmp/synapse-provider-tmux"
  end
  set -l provider_pidfile "$provider_runtime/$key.pid"
  if test -r "$provider_pidfile"
    set -l ppid (cat "$provider_pidfile" 2>/dev/null)
    if test -n "$ppid"; and kill -0 "$ppid" 2>/dev/null
      return 0
    end
  end
  nohup synapse arm --name "$identity-rx" --for "$project" --directed-only \
    >"$logfile" 2>&1 &
  echo $last_pid >"$pidfile"
  disown $last_pid 2>/dev/null
end

function __synapse_release_waiter
  # Stop the passive prompt-armed waiter (by its pidfile) so an interactive
  # provider's own tmux waker can own "$identity-rx" without a name collision.
  # The waiter is killed, not merely superseded, so it does not re-arm and fight.
  test -n "$SYN_IDENTITY"; or return 0
  set -l runtime "$XDG_RUNTIME_DIR/synapse-shell"
  if test -z "$XDG_RUNTIME_DIR"
    set runtime "/tmp/synapse-shell"
  end
  set -l key (__synapse_safe_key "$SYN_IDENTITY")
  set -l pidfile "$runtime/$key.pid"
  test -r "$pidfile"; or return 0
  set -l pid (cat "$pidfile" 2>/dev/null)
  if test -n "$pid"
    kill "$pid" 2>/dev/null
  end
  rm -f "$pidfile" 2>/dev/null
end

function __synapse_run_provider
  set -l command_name "$argv[1]"
  set -e argv[1]
  __synapse_auto_arm >/dev/null 2>&1; or true
  if command -q synapse
    __synapse_release_waiter >/dev/null 2>&1; or true
    synapse worker-session --project "$SYN_PROJECT" --identity "$SYN_IDENTITY" -- \
      "$command_name" $argv
  else
    command "$command_name" $argv
  end
end

{providers}__synapse_auto_arm >/dev/null 2>&1; or true
"""


def render_shell_hook(
    *,
    shell: str = "bash",
    provider_commands: tuple[str, ...] = DEFAULT_PROVIDER_COMMANDS,
) -> str:
    """Render shell code that auto-arms the configured project and wraps providers.

    The hook keeps fresh terminals connected without silently claiming a project
    from the current working directory. Project identity is explicit via
    ``SYN_PROJECT``/``SYN_IDENTITY`` or opt-in via ``.synapse/project``. Without
    either, the terminal joins ``SYNAPSE_DEFAULT_PROJECT`` or the neutral
    ``user`` lane. Provider wrappers route commands through ``synapse
    worker-session`` so Codex, Claude, Gemini, and local commands inherit the
    same identity without manual setup.
    """
    resolved = normalise_shell(shell)
    if resolved == "fish":
        return _render_fish_shell_hook(provider_commands)
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
__synapse_marker_project() {{
  local top marker project
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
  marker="$top/.synapse/project"
  [ -r "$marker" ] || return 1
  project="$(sed -n '1{{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}}' "$marker" 2>/dev/null)"
  if [ -n "$project" ]; then
    printf '%s\\n' "$project"
  else
    basename "$top"
  fi
}}

__synapse_cwd_project() {{
  local top
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || top="$PWD"
  basename "$top"
}}

__synapse_project() {{
  local marker_project
  if [ -n "${{SYN_IDENTITY:-}}" ] && [ "${{__SYNAPSE_AUTO_IDENTITY:-}}" != "$SYN_IDENTITY" ]; then
    case "$SYN_IDENTITY" in
      */*) printf '%s\\n' "${{SYN_IDENTITY%%/*}}"; return 0 ;;
    esac
  fi
  if [ "${{SYNAPSE_AUTO_PROJECT_FROM_CWD:-0}}" = "1" ]; then
    __synapse_cwd_project
    return $?
  fi
  if marker_project="$(__synapse_marker_project)"; then
    printf '%s\\n' "$marker_project"
    return 0
  fi
  printf '%s\\n' "${{SYNAPSE_DEFAULT_PROJECT:-user}}"
}}

__synapse_safe_key() {{
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}}

__synapse_auto_arm() {{
  [ "${{SYNAPSE_AUTO_CONNECT:-1}}" = "0" ] && return 0
  command -v synapse >/dev/null 2>&1 || return 0
  local project identity terminal_id runtime key pidfile logfile pid provider_pidfile

  if [ -n "${{SYN_PROJECT:-}}" ] && [ "${{__SYNAPSE_AUTO_PROJECT:-}}" != "$SYN_PROJECT" ]; then
    project="$SYN_PROJECT"
  else
    project="$(__synapse_project)" || return 0
    [ -n "$project" ] || return 0
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
  # Yield to an active worker-session tmux waker: it owns "$identity-rx" and can
  # actually wake the agent's pane, so a second passive waiter on the same name
  # would only collide and lock the injecting waker out.
  provider_pidfile="${{XDG_RUNTIME_DIR:-/tmp}}/synapse-provider-tmux/$key.pid"
  if [ -r "$provider_pidfile" ]; then
    pid="$(cat "$provider_pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  nohup synapse arm --name "$identity-rx" --for "$project" --directed-only \
    >"$logfile" 2>&1 &
  printf '%s\n' "$!" >"$pidfile"
}}

__synapse_release_waiter() {{
  # Stop the passive prompt-armed waiter (by its pidfile) so an interactive
  # provider's own tmux waker can own "$identity-rx" without a name collision.
  # The waiter is killed, not merely superseded, so it does not re-arm and fight.
  local runtime key pidfile pid
  [ -n "${{SYN_IDENTITY:-}}" ] || return 0
  runtime="${{XDG_RUNTIME_DIR:-/tmp}}/synapse-shell"
  key="$(__synapse_safe_key "$SYN_IDENTITY")"
  pidfile="$runtime/$key.pid"
  [ -r "$pidfile" ] || return 0
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  rm -f "$pidfile" 2>/dev/null
  return 0
}}

__synapse_run_provider() {{
  local command_name="$1"
  shift
  __synapse_auto_arm || true
  if command -v synapse >/dev/null 2>&1; then
    __synapse_release_waiter || true
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
    if resolved == "fish":
        return (
            f"{START_MARKER}\n"
            f"if command -q {shlex.quote(synapse_bin)}\n"
            f"  {shlex.quote(synapse_bin)} shell-hook --shell fish | source\n"
            "end\n"
            f"{END_MARKER}\n"
        )
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
    block = render_rc_block(shell=resolved, synapse_bin=synapse_bin)  # nosec B604
    separator = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{separator}{block}", encoding="utf-8")
    return [f"installed shell hook in {path}", "open a new terminal or source the file"]
