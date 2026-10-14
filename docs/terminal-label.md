<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — terminal tab label and status lines
-->

# Terminal label

With several agent terminals open, a tab that says `tmux` tells you nothing.
`synapse label` makes the terminal say who works in it:

- the **tab label** is short: the group's abbreviation, the project when a
  specific focus is assigned to the seat, and the vendor;
- the **status lines** at the bottom of the terminal wrap the declared details
  within tmux's five-line limit. Overflow is truncated; `--show` prints every
  detail in full.

```text
tab:     ACME-PT · billing-api · vendor-x

status:  group: ACME-PLATFORM-TEAM (ACME-PT)  |  project: billing-api · focus: refund flow
         vendor: vendor-x · model: model-7  |  seat: billing-api/agent-2  |  session: night-shift
         native session: 0f3c…  |  terminal: user/terminal-4711
```

It works on the tmux session the command runs in. Outside tmux the command
does nothing and exits 0.

## Use

```bash
synapse label --focus "refund flow" --vendor vendor-x --model model-7
synapse label --session-name night-shift          # adds to what is stored
synapse label --show                              # print the label and every detail
synapse label --restore                           # put the earlier label and status back
```

| Option | Meaning |
| --- | --- |
| `--identity` | Seat identity, `PROJECT/seat`. Defaults to `SYN_IDENTITY`, which is used only until a seat is stored on the session. Connection suffixes such as `-rx`, `-tx` and `-presence` are dropped. |
| `--focus` | The specific focus assigned to the seat. Without one the tab label omits the project. |
| `--vendor`, `--model` | Vendor and exact model id. Declared values; the command never guesses them. |
| `--session-name`, `--native-session`, `--terminal-identity` | Further details for the status lines. |
| `--session` | Exact tmux session; defaults to the one the command runs in. |
| `--profile` | Group profile file; see below. |
| `--show` | Print the label and all details, unwrapped; change nothing. |
| `--refresh` | Lay the stored details out again for the current width. The resize hook calls this. |
| `--restore` | Put back the label and status the session had before the first call. |

A detail nobody declared is shown as `not declared`.

Details are stored on the session, so each call adds to the previous ones and
`--refresh` needs no arguments.

## Groups and abbreviations

The command does not know your organisation. Group names, abbreviations and
member projects come from a profile file,
`$XDG_CONFIG_HOME/synapse/terminal_label_profile.json` (by default
`~/.config/synapse/terminal_label_profile.json`):

```json
{
  "groups": {
    "ACME-PLATFORM-TEAM": {
      "abbreviation": "ACME-PT",
      "projects": ["billing-api", "ledger"]
    },
    "ACME-RESEARCH-AND-TOOLS": {
      "projects": ["notebooks"]
    }
  }
}
```

The project is the part of the seat identity before the first `/`. Its group
is the one that lists it, or the group of that very name.

A group without an `abbreviation` gets a derived one: the initials of its
words, keeping a first word of at most four letters whole
(`ACME-RESEARCH-AND-TOOLS` gives `ACME-RAT`). The status line marks it
`derived`. Without a profile, or for a project no group lists, the group is
`not declared` and the tab label starts with the project.

A profile file that exists but cannot be read as this shape is an error (exit
status 2), not an empty profile.

## Width and wrapping

The details are packed in a fixed order into lines no wider than the terminal:
the smallest attached client decides, or the window width when no client is
attached. tmux draws at most five status lines.

- A detail longer than a line is cut and ends with `…`.
- When five lines are not enough, the fifth ends with `…`.
- `synapse label --show` always prints every detail complete.

When the terminal is resized or a client attaches, a session hook runs
`synapse label --refresh` and the lines are laid out again. Nothing polls.

## What the command changes, and how to undo it

Only options of the one named session are written: `set-titles`,
`set-titles-string`, `status`, `status-format[0]` to `status-format[4]`, the
hooks `client-resized` and `client-attached`, and two options of its own that
hold the details and the earlier values. No global option and no other session
is touched; the session is addressed in tmux's exact-match form, so a name
that is only the beginning of another session's name reaches nothing.

The status lines replace the session's usual status content, the window list.
The values the session had before the first call are saved, and
`synapse label --restore` puts them back exactly and removes the hooks.

To switch the feature off for a terminal, set `SYNAPSE_TERMINAL_LABEL=off` in
its environment.

## Text is shown, never run

tmux expands format sequences in a title and in a status line; `#(command)`
there runs the command. An identity, a focus or a model id is text somebody
else may have chosen. Before any such value reaches tmux, control characters
are removed and every `#` is doubled, so tmux displays `#(…)`, `#[…]` and
`#{…}` as they were typed and expands nothing.

## Limits

- tmux only. A terminal without tmux is left alone.
- The label is set when `synapse label` runs. Starting a session through
  `synapse agent-tmux start` or `synapse worker-session` does not call it yet.
- The tab shows the label only when the outer terminal honours title changes
  from tmux.
