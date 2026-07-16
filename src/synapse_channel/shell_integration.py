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
import re
import shlex
from pathlib import Path

from synapse_channel.reap import provider_runtime_dir

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
_PROVIDER_COMMAND_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.+-]*\Z")
"""Bare command names safe as Bash, Zsh, and Fish wrapper declarations."""


def pid_is_live_process(pid: int) -> bool:
    """Return whether ``pid`` is a non-zombie process.

    ``os.kill(pid, 0)`` succeeds for zombie entries still in the process table,
    so a defunct agent-tmux would look "active" forever and keep plain waiters
    yielding (or block waiter restart). Read ``/proc/<pid>/stat`` and reject
    state ``Z``.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        # /proc/<pid>/stat: pid (comm) state ... — state is the field after the
        # closing paren of the comm, which may itself contain spaces/parens.
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    close = stat_text.rfind(")")
    if close < 0 or close + 2 >= len(stat_text):
        return False
    state = stat_text[close + 2 : close + 3]
    return state != "Z"


def has_active_tmux_provider(identity: str) -> bool:
    """Return True if an active tmux provider holds this identity's live waker.

    Used by passive arm/wait logic and harnesses to yield early and avoid name
    collisions on the *-rx sidecar. The provider owns the name for pane injection
    (WAKE_PANE_BRIDGE / receiver wake capability). Mirrors the pidfile check in the
    generated shell hooks (XDG_RUNTIME_DIR/synapse-provider-tmux/*.pid, or a
    private cache fallback when XDG runtime is unset).

    ``SYN_TMUX_PROVIDER=1`` (running as the inner agent of a provider session)
    counts only for the SESSION'S OWN identity (``$SYN_IDENTITY``): the provider
    wakes that seat and no other. The flag must never suppress an arm that
    explicitly names a different identity — ambient environment is a statement
    about this session, not about every seat reachable from it (2026-07-10 P0:
    the identity-blind form made explicitly named waiters refuse to arm while
    any provider session was live, and directed messages were lost).
    """
    if (
        os.environ.get("SYN_TMUX_PROVIDER") == "1"
        and identity == os.environ.get("SYN_IDENTITY", "").strip()
    ):
        return True
    runtime = provider_runtime_dir()
    key = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in identity)
    pidfile = runtime / f"{key}.pid"
    if not pidfile.is_file():
        return False
    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        return False
    return pid_is_live_process(pid)


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


def _provider_command_name(command: str) -> str:
    """Return a validated bare provider command name."""
    name = command.strip()
    if _PROVIDER_COMMAND_RE.fullmatch(name) is None:
        raise ValueError(
            "provider command must be a bare name using letters, digits, '_', '.', '+', or '-'"
        )
    return name


def _provider_function(command: str) -> str:
    """Render one provider wrapper function."""
    name = _provider_command_name(command)
    quoted = shlex.quote(name)
    return (
        f"{name}() {{\n"
        "  __synapse_auto_arm || true\n"
        f"  if command -v synapse >/dev/null 2>&1; then\n"
        "    __synapse_release_waiter || true\n"
        '    SYNAPSE_AUTO_CONNECT=0 synapse worker-session --project="$SYN_PROJECT" '
        f'--identity="$SYN_IDENTITY" -- {quoted} "$@"\n'
        "  else\n"
        f'    command {quoted} "$@"\n'
        "  fi\n"
        "}\n"
    )


def _fish_provider_function(command: str) -> str:
    """Render one Fish provider wrapper function."""
    name = _provider_command_name(command)
    quoted = shlex.quote(name)
    return (
        f"function {name} --wraps {quoted}\n"
        "  __synapse_auto_arm >/dev/null 2>&1; or true\n"
        "  if command -q synapse\n"
        "    __synapse_release_waiter >/dev/null 2>&1; or true\n"
        '    env SYNAPSE_AUTO_CONNECT=0 synapse worker-session --project="$SYN_PROJECT" '
        f'--identity="$SYN_IDENTITY" -- {quoted} $argv\n'
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
    if not __synapse_identity_is_foreign_auto "$SYN_IDENTITY"
      if string match -q "*/*" "$SYN_IDENTITY"
        string split -m1 / "$SYN_IDENTITY" | head -n 1
        return 0
      end
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

function __synapse_pid_in_session_lineage
  # 0 when $argv[1] is this shell or a live ancestor of it (bounded /proc walk).
  set -l probe (echo %self)
  set -l hops 0
  while test -n "$probe"; and test "$probe" -gt 1 2>/dev/null; and test $hops -lt 64
    if test "$probe" = "$argv[1]"
      return 0
    end
    set probe (awk '/^PPid:/{{print $2}}' "/proc/$probe/status" 2>/dev/null)
    set hops (math $hops + 1)
  end
  return 1
end

function __synapse_identity_is_foreign_auto
  # 0 when $argv[1] is a default-shape auto identity (<project>/terminal-<pid>)
  # minted by a shell OUTSIDE this session's lineage — the shared-name
  # collision of the 2026-07-16 delivery-integrity incident (DEL-INT-C).
  # Manual and provider identities are never judged foreign; an explicit
  # provider session (SYN_TMUX_PROVIDER=1) keeps its handed-down identity;
  # without /proc the answer is "not foreign" (fail-open).
  if not string match -q "*/terminal-*" -- "$argv[1]"
    return 1
  end
  set -l suffix (string replace -r '^.*/terminal-' '' -- "$argv[1]")
  if not string match -qr '^[0-9]+$' -- "$suffix"
    return 1
  end
  if test "$SYN_TMUX_PROVIDER" = "1"
    return 1
  end
  if not test -d /proc
    return 1
  end
  if __synapse_pid_in_session_lineage "$suffix"
    return 1
  end
  return 0
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

  # A numeric terminal id inherited from outside this session's lineage must
  # never name this session: it is how one shell's exported id resurrects the
  # shared name on every later prompt, even after a re-mint.
  if string match -qr '^[0-9]+$' -- "$terminal_id"; and test -d /proc
    if test "$SYN_TMUX_PROVIDER" != "1"; and not __synapse_pid_in_session_lineage "$terminal_id"
      set terminal_id (echo %self)
    end
  end

  set -l identity
  set -l keep_inherited 0
  if test -n "$SYN_IDENTITY"; and test "$__SYNAPSE_AUTO_IDENTITY" != "$SYN_IDENTITY"
    if not __synapse_identity_is_foreign_auto "$SYN_IDENTITY"
      set keep_inherited 1
    end
  end
  if test $keep_inherited = 1
    set identity "$SYN_IDENTITY"
  else
    if test -n "$SYN_IDENTITY"; and __synapse_identity_is_foreign_auto "$SYN_IDENTITY"
      printf 'synapse: %s was minted outside this session; re-minting.\\n' \\
        "$SYN_IDENTITY" >&2
    end
    set identity "$project/terminal-$terminal_id"
    if test -n "$SYN_AGENT_TYPE"
      set identity "$project/$SYN_AGENT_TYPE-$terminal_id"
    end
    set -gx SYN_IDENTITY "$identity"
    set -gx __SYNAPSE_AUTO_IDENTITY "$identity"
  end

  # Prefer XDG runtime; fall back to private cache (never shared /tmp/synapse-*).
  set -l runtime
  if test -n "$XDG_RUNTIME_DIR"
    set runtime "$XDG_RUNTIME_DIR/synapse-shell"
  else
    set -l cache_home "$XDG_CACHE_HOME"
    if test -z "$cache_home"
      set cache_home "$HOME/.cache"
    end
    if test -n "$cache_home"
      set runtime "$cache_home/synapse-shell"
    else
      set runtime "/tmp/synapse-shell-"(id -u)
    end
  end
  mkdir -p -m 700 "$runtime" 2>/dev/null; or return 0
  # -m 700 only applies on creation; re-tighten a pre-existing dir and refuse one
  # we do not solely own or that is a symlink, so a precreated 0777 or planted
  # runtime cannot host our pidfile/logfile (fail closed: no waiter over an unsafe dir).
  chmod 700 "$runtime" 2>/dev/null
  if not test -d "$runtime"; or test -L "$runtime"; or not test -O "$runtime"
    return 0
  end
  set -l key (__synapse_safe_key "$identity")
  set -l pidfile "$runtime/$key.pid"
  set -l logfile "$runtime/$key.log"
  if test -r "$pidfile"
    set -l pid (cat "$pidfile" 2>/dev/null)
    if test -n "$pid"; and kill -0 "$pid" 2>/dev/null
      return 0
    end
  end
  # Yield to an active worker-session tmux waker (or explicit provider session):
  # it owns "$identity-rx" for the session lifetime and injects wake prompts.
  # A second passive waiter would collide. SYN_TMUX_PROVIDER=1 marks inner agent.
  if test "$SYN_TMUX_PROVIDER" = "1"
    return 0
  end
  set -l provider_runtime
  if test -n "$XDG_RUNTIME_DIR"
    set provider_runtime "$XDG_RUNTIME_DIR/synapse-provider-tmux"
  else
    set -l cache_home "$XDG_CACHE_HOME"
    if test -z "$cache_home"
      set cache_home "$HOME/.cache"
    end
    if test -n "$cache_home"
      set provider_runtime "$cache_home/synapse-provider-tmux"
    else
      set provider_runtime "/tmp/synapse-provider-tmux-"(id -u)
    end
  end
  set -l provider_pidfile "$provider_runtime/$key.pid"
  if test -r "$provider_pidfile"
    set -l ppid (cat "$provider_pidfile" 2>/dev/null)
    if test -n "$ppid"; and kill -0 "$ppid" 2>/dev/null
      return 0
    end
  end
  nohup synapse arm --name="$identity-rx" --for="$identity" --directed-only \
    --owner-pid $fish_pid >"$logfile" 2>&1 &
  echo $last_pid >"$pidfile"
  disown $last_pid 2>/dev/null
end

function __synapse_release_waiter
  # Stop the passive prompt-armed waiter (by its pidfile) so an interactive
  # provider's own tmux waker can own "$identity-rx" without a name collision.
  # The waiter is killed, not merely superseded, so it does not re-arm and fight.
  test -n "$SYN_IDENTITY"; or return 0
  set -l runtime
  if test -n "$XDG_RUNTIME_DIR"
    set runtime "$XDG_RUNTIME_DIR/synapse-shell"
  else
    set -l cache_home "$XDG_CACHE_HOME"
    if test -z "$cache_home"
      set cache_home "$HOME/.cache"
    end
    if test -n "$cache_home"
      set runtime "$cache_home/synapse-shell"
    else
      set runtime "/tmp/synapse-shell-"(id -u)
    end
  end
  set -l key (__synapse_safe_key "$SYN_IDENTITY")
  set -l pidfile "$runtime/$key.pid"
  test -r "$pidfile"; or return 0
  set -l pid (cat "$pidfile" 2>/dev/null)
  if test -n "$pid"
    # Verify the PID is THIS identity's synapse arm waiter before signalling it, so
    # a planted pidfile cannot turn this release into a blind kill of an unrelated
    # process (mirrors reap.py's argv check; fail closed when the argv is unreadable).
    set -l cmdline
    if test -r "/proc/$pid/cmdline"
      set cmdline (tr '\\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
    else
      set cmdline (ps -o args= -p "$pid" 2>/dev/null)
    end
    if string match -q -- "* arm *--name=$SYN_IDENTITY-rx*" "$cmdline"
      kill "$pid" 2>/dev/null
    end
  end
  rm -f "$pidfile" 2>/dev/null
end

function __synapse_run_provider
  set -l command_name "$argv[1]"
  set -e argv[1]
  __synapse_auto_arm >/dev/null 2>&1; or true
  if command -q synapse
    __synapse_release_waiter >/dev/null 2>&1; or true
    env SYNAPSE_AUTO_CONNECT=0 synapse worker-session --project="$SYN_PROJECT" \
      --identity="$SYN_IDENTITY" -- "$command_name" $argv
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
  if [ -n "${{SYN_IDENTITY:-}}" ] && [ "${{__SYNAPSE_AUTO_IDENTITY:-}}" != "$SYN_IDENTITY" ] \\
      && ! __synapse_identity_is_foreign_auto "$SYN_IDENTITY"; then
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

__synapse_pid_in_session_lineage() {{
  # 0 when $1 is this shell or a live ancestor of it (bounded /proc walk).
  local probe hops
  probe=$$
  hops=0
  while [ -n "$probe" ] && [ "$probe" -gt 1 ] 2>/dev/null && [ "$hops" -lt 64 ]; do
    [ "$probe" = "$1" ] && return 0
    probe="$(awk '/^PPid:/{{print $2}}' "/proc/$probe/status" 2>/dev/null)" || return 1
    hops=$((hops + 1))
  done
  return 1
}}

__synapse_identity_is_foreign_auto() {{
  # 0 when $1 is a default-shape auto identity (<project>/terminal-<pid>)
  # minted by a shell OUTSIDE this session's lineage. Environment layering
  # (tmux server env, systemd user env) carries such an identity into
  # unrelated sessions while the mint-guard variable diverges, so every seat
  # on the workstation silently coordinates under one shared name — the
  # 2026-07-16 delivery-integrity incident (DEL-INT-C). Manual and provider
  # identities (non-terminal shapes, non-numeric ids) are never judged
  # foreign, an explicit provider session (SYN_TMUX_PROVIDER=1) keeps its
  # handed-down identity, and without /proc the answer is "not foreign"
  # (fail-open to the old behaviour).
  local suffix
  case "$1" in
    */terminal-*) suffix="${{1##*/terminal-}}" ;;
    *) return 1 ;;
  esac
  case "$suffix" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "${{SYN_TMUX_PROVIDER:-0}}" = "1" ] && return 1
  [ -d /proc ] || return 1
  __synapse_pid_in_session_lineage "$suffix" && return 1
  return 0
}}

__synapse_auto_arm() {{
  [ "${{SYNAPSE_AUTO_CONNECT:-1}}" = "0" ] && return 0
  command -v synapse >/dev/null 2>&1 || return 0
  local project identity terminal_id runtime key pidfile logfile pid provider_pidfile
  local cache provider_runtime

  if [ -n "${{SYN_PROJECT:-}}" ] && [ "${{__SYNAPSE_AUTO_PROJECT:-}}" != "$SYN_PROJECT" ]; then
    project="$SYN_PROJECT"
  else
    project="$(__synapse_project)" || return 0
    [ -n "$project" ] || return 0
    export SYN_PROJECT="$project"
    export __SYNAPSE_AUTO_PROJECT="$project"
  fi

  terminal_id="${{SYN_AGENT_ID:-${{SYNAPSE_TERMINAL_ID:-$$}}}}"
  # A numeric terminal id inherited from outside this session's lineage must
  # never name this session: it is how one shell's exported id resurrects the
  # shared name on every later prompt, even after a re-mint.
  case "$terminal_id" in
    ''|*[!0-9]*) : ;;
    *) if [ -d /proc ] && [ "${{SYN_TMUX_PROVIDER:-0}}" != "1" ] \\
          && ! __synapse_pid_in_session_lineage "$terminal_id"; then
         terminal_id=$$
       fi ;;
  esac
  if [ -n "${{SYN_IDENTITY:-}}" ] && [ "${{__SYNAPSE_AUTO_IDENTITY:-}}" != "$SYN_IDENTITY" ] \\
      && ! __synapse_identity_is_foreign_auto "$SYN_IDENTITY"; then
    identity="$SYN_IDENTITY"
  else
    if [ -n "${{SYN_IDENTITY:-}}" ] && __synapse_identity_is_foreign_auto "$SYN_IDENTITY"; then
      printf 'synapse: %s was minted outside this session; re-minting.\\n' \\
        "$SYN_IDENTITY" >&2
    fi
    identity="$project/${{SYN_AGENT_TYPE:-terminal}}-$terminal_id"
    export SYN_IDENTITY="$identity"
    export __SYNAPSE_AUTO_IDENTITY="$identity"
  fi

  # Prefer XDG runtime; fall back to private cache (never shared /tmp/synapse-*).
  if [ -n "${{XDG_RUNTIME_DIR:-}}" ]; then
    runtime="$XDG_RUNTIME_DIR/synapse-shell"
  else
    cache="${{XDG_CACHE_HOME:-${{HOME:-}}/.cache}}"
    if [ -n "$cache" ] && [ "$cache" != "/.cache" ]; then
      runtime="$cache/synapse-shell"
    else
      runtime="${{TMPDIR:-/tmp}}/synapse-shell-$(id -u)"
    fi
  fi
  mkdir -p -m 700 "$runtime" 2>/dev/null || return 0
  # -m 700 only applies on creation; re-tighten a pre-existing dir and refuse one
  # we do not solely own or that is a symlink, so a precreated 0777 or planted
  # runtime cannot host our pidfile/logfile (fail closed: no waiter over an unsafe dir).
  chmod 700 "$runtime" 2>/dev/null
  if [ ! -d "$runtime" ] || [ -L "$runtime" ] || [ ! -O "$runtime" ]; then
    return 0
  fi
  key="$(__synapse_safe_key "$identity")"
  pidfile="$runtime/$key.pid"
  logfile="$runtime/$key.log"
  if [ -r "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  # Yield to an active worker-session tmux waker (or SYN_TMUX_PROVIDER=1 session):
  # it owns "$identity-rx" for the session lifetime. Plain passive would collide.
  if [ "${{SYN_TMUX_PROVIDER:-}}" = "1" ]; then
    return 0
  fi
  if [ -n "${{XDG_RUNTIME_DIR:-}}" ]; then
    provider_runtime="$XDG_RUNTIME_DIR/synapse-provider-tmux"
  else
    cache="${{XDG_CACHE_HOME:-${{HOME:-}}/.cache}}"
    if [ -n "$cache" ] && [ "$cache" != "/.cache" ]; then
      provider_runtime="$cache/synapse-provider-tmux"
    else
      provider_runtime="${{TMPDIR:-/tmp}}/synapse-provider-tmux-$(id -u)"
    fi
  fi
  provider_pidfile="$provider_runtime/$key.pid"
  if [ -r "$provider_pidfile" ]; then
    pid="$(cat "$provider_pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  nohup synapse arm --name="$identity-rx" --for="$identity" --directed-only \
    --owner-pid $$ >"$logfile" 2>&1 &
  printf '%s\n' "$!" >"$pidfile"
}}

__synapse_release_waiter() {{
  # Stop the passive prompt-armed waiter (by its pidfile) so an interactive
  # provider's own tmux waker can own "$identity-rx" without a name collision.
  # The waiter is killed, not merely superseded, so it does not re-arm and fight.
  local runtime key pidfile pid cache cmdline
  [ -n "${{SYN_IDENTITY:-}}" ] || return 0
  if [ -n "${{XDG_RUNTIME_DIR:-}}" ]; then
    runtime="$XDG_RUNTIME_DIR/synapse-shell"
  else
    cache="${{XDG_CACHE_HOME:-${{HOME:-}}/.cache}}"
    if [ -n "$cache" ] && [ "$cache" != "/.cache" ]; then
      runtime="$cache/synapse-shell"
    else
      runtime="${{TMPDIR:-/tmp}}/synapse-shell-$(id -u)"
    fi
  fi
  key="$(__synapse_safe_key "$SYN_IDENTITY")"
  pidfile="$runtime/$key.pid"
  [ -r "$pidfile" ] || return 0
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    # Verify the PID is THIS identity's synapse arm waiter before signalling it, so
    # a planted pidfile cannot turn this release into a blind kill of an unrelated
    # process (mirrors reap.py's argv check; fail closed when the argv is unreadable).
    cmdline=""
    if [ -r "/proc/$pid/cmdline" ]; then
      cmdline="$(tr '\\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
    else
      cmdline="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    fi
    case "$cmdline" in
      *" arm "*"--name=$SYN_IDENTITY-rx"*) kill "$pid" 2>/dev/null ;;
    esac
  fi
  rm -f "$pidfile" 2>/dev/null
  return 0
}}

__synapse_run_provider() {{
  local command_name="$1"
  shift
  __synapse_auto_arm || true
  if command -v synapse >/dev/null 2>&1; then
    __synapse_release_waiter || true
    SYNAPSE_AUTO_CONNECT=0 synapse worker-session --project="$SYN_PROJECT" \
      --identity="$SYN_IDENTITY" -- "$command_name" "$@"
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
            f"if command -q -- {shlex.quote(synapse_bin)}\n"
            f"  {shlex.quote(synapse_bin)} shell-hook --shell fish | source\n"
            "end\n"
            f"{END_MARKER}\n"
        )
    return (
        f"{START_MARKER}\n"
        f"if command -v -- {shlex.quote(synapse_bin)} >/dev/null 2>&1; then\n"
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
