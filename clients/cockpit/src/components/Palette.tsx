// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the command palette: the whole cockpit from the keyboard

import { useEffect, useRef, useState } from "react";

import {
  matchCommands,
  sendOperatorMessage,
  type Command,
  type OperatorSendResult,
} from "../lib/palette";

function sendResultLine(result: OperatorSendResult): string {
  if (result.kind === "sent")
    return result.detail === ""
      ? "sent — the hub authorised and recorded it"
      : `sent — ${result.detail}`;
  if (result.kind === "undelivered")
    return result.detail === ""
      ? "relayed, not delivered — no online recipient matched"
      : `relayed, not delivered — ${result.detail}`;
  if (result.kind === "not-armed")
    return "operator write-path not armed on this dashboard (--operator)";
  if (result.kind === "refused") return `refused: ${result.reason}`;
  return `failed: ${result.message}`;
}

interface PaletteProps {
  readonly open: boolean;
  readonly commands: readonly Command[];
  readonly onClose: () => void;
  /** Executes a chosen non-write command. */
  readonly onRun: (command: Command) => void;
}

/**
 * Ctrl/Cmd+K. Navigation commands dispatch to the caller and close. The one
 * write command opens an inline two-field form and states the relay's
 * outcome verbatim — including "not armed", which is a fact about the
 * dashboard's posture, not an error.
 */
export function Palette({ open, commands, onClose, onRun }: PaletteProps): JSX.Element | null {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [composing, setComposing] = useState(false);
  const [to, setTo] = useState("");
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      setComposing(false);
      setOutcome(null);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Escape closes wherever focus sits — a palette that only closes from its
  // own input is a trap after tabbing to a button.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const matches = matchCommands(commands, query);
  const clamped = Math.min(cursor, Math.max(0, matches.length - 1));

  const choose = (command: Command): void => {
    if (command.kind === "operator-message") {
      setComposing(true);
      setOutcome(null);
      return;
    }
    onRun(command);
    onClose();
  };

  const submitSend = (): void => {
    setOutcome("relaying…");
    void sendOperatorMessage(to, text).then((result) => setOutcome(sendResultLine(result)));
  };

  return (
    <div className="drawer-veil" onClick={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-label="Command palette"
        onClick={(click) => click.stopPropagation()}
      >
        {!composing ? (
          <>
            <input
              ref={inputRef}
              className="palette__input"
              value={query}
              placeholder="type a command, an agent, or a task…"
              aria-label="Search commands"
              onChange={(change) => {
                setQuery(change.target.value);
                setCursor(0);
              }}
              onKeyDown={(key) => {
                if (key.key === "ArrowDown") {
                  key.preventDefault();
                  setCursor((current) => Math.min(current + 1, matches.length - 1));
                } else if (key.key === "ArrowUp") {
                  key.preventDefault();
                  setCursor((current) => Math.max(current - 1, 0));
                } else if (key.key === "Enter") {
                  const chosen = matches[clamped];
                  if (chosen !== undefined) choose(chosen);
                } else if (key.key === "Escape") onClose();
              }}
            />
            <ul className="palette__list" role="listbox">
              {matches.length === 0 && <li className="palette__empty">no command matches</li>}
              {matches.map((command, index) => (
                <li key={command.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={index === clamped}
                    className={`palette__item${index === clamped ? " palette__item--active" : ""}${
                      command.kind === "operator-message" ? " palette__item--write" : ""
                    }`}
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => choose(command)}
                  >
                    {command.title}
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <div className="palette__compose">
            <span className="palette__compose-head">
              operator message — relayed by the dashboard, authorised and audited by the hub
            </span>
            <input
              className="palette__input"
              value={to}
              placeholder="to (identity, e.g. PROJECT/agent)"
              aria-label="Message recipient"
              onChange={(change) => setTo(change.target.value)}
            />
            <input
              className="palette__input"
              value={text}
              placeholder="text"
              aria-label="Message text"
              onChange={(change) => setText(change.target.value)}
              onKeyDown={(key) => {
                if (key.key === "Enter" && to.trim() !== "" && text.trim() !== "") submitSend();
                if (key.key === "Escape") onClose();
              }}
            />
            <div className="palette__compose-row">
              <button
                type="button"
                className="log-controls__toggle"
                disabled={to.trim() === "" || text.trim() === ""}
                onClick={submitSend}
              >
                send
              </button>
              <button type="button" className="panel__clear" onClick={() => setComposing(false)}>
                back
              </button>
              {outcome !== null && <span className="palette__outcome">{outcome}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
