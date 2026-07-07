// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the command palette's brain: commands, ranking, and the one write

// Ctrl/Cmd+K opens a list of everything the cockpit can do from the
// keyboard. Navigation commands only steer panels the pointer already
// steers. The single WRITE command — send a message through the dashboard's
// operator relay — is fail-closed three times over: the server route 404s
// unless the dashboard was armed with --operator, the hub still authorises
// and audits the relayed frame, and the palette states "not armed" instead
// of pretending; the cockpit itself arms nothing.

/** What a palette entry does when chosen. */
export type CommandKind =
  | "focus-agent"
  | "inspect-agent"
  | "inspect-task"
  | "trace-task"
  | "toggle-theme"
  | "toggle-density"
  | "toggle-travel"
  | "clear-focus"
  | "operator-message";

/** One palette entry. */
export interface Command {
  readonly id: string;
  readonly kind: CommandKind;
  readonly title: string;
  /** The argument the kind acts on (agent name, task id); "" for toggles. */
  readonly subject: string;
  /** Extra matched text (project names, aliases). */
  readonly keywords: string;
}

/** Build the command list from what the fleet currently shows. */
export function buildCommands(
  agents: readonly string[],
  taskIds: readonly string[],
): Command[] {
  const commands: Command[] = [
    { id: "toggle-theme", kind: "toggle-theme", title: "toggle theme (dark / light)", subject: "", keywords: "palette colours" },
    { id: "toggle-density", kind: "toggle-density", title: "toggle density (cozy / compact)", subject: "", keywords: "rows spacing" },
    { id: "toggle-travel", kind: "toggle-travel", title: "time travel (scrub fleet state)", subject: "", keywords: "history replay state-at" },
    { id: "clear-focus", kind: "clear-focus", title: "clear focus lens", subject: "", keywords: "my work reset" },
    { id: "operator-message", kind: "operator-message", title: "operator: send a message…", subject: "", keywords: "write chat relay say" },
  ];
  for (const agent of agents) {
    commands.push(
      { id: `focus:${agent}`, kind: "focus-agent", title: `focus ${agent}`, subject: agent, keywords: "lens my work" },
      { id: `agent:${agent}`, kind: "inspect-agent", title: `inspect agent ${agent}`, subject: agent, keywords: "drawer detail" },
    );
  }
  for (const taskId of taskIds) {
    commands.push(
      { id: `task:${taskId}`, kind: "inspect-task", title: `inspect task ${taskId}`, subject: taskId, keywords: "drawer detail board" },
      { id: `trace:${taskId}`, kind: "trace-task", title: `trace ${taskId}`, subject: taskId, keywords: "causality causes effects" },
    );
  }
  return commands;
}

/** How many matches the palette shows. */
export const PALETTE_SHOWN = 12;

/**
 * Rank commands against a query: prefix beats word-start beats substring,
 * over title first and keywords second; an empty query shows the toggles
 * and the write (the static head of the list).
 */
export function matchCommands(commands: readonly Command[], query: string): Command[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return commands.slice(0, PALETTE_SHOWN);
  const scored: { command: Command; score: number }[] = [];
  for (const command of commands) {
    const title = command.title.toLowerCase();
    const keywords = command.keywords.toLowerCase();
    let score = -1;
    if (title.startsWith(needle)) score = 0;
    else if (title.includes(` ${needle}`)) score = 1;
    else if (title.includes(needle)) score = 2;
    else if (keywords.includes(needle)) score = 3;
    if (score >= 0) scored.push({ command, score });
  }
  scored.sort((a, b) => a.score - b.score || a.command.title.localeCompare(b.command.title));
  return scored.slice(0, PALETTE_SHOWN).map((entry) => entry.command);
}

/** The outcome of the one write, stated plainly. */
export type OperatorSendResult =
  | { readonly kind: "sent"; readonly detail: string }
  | { readonly kind: "undelivered"; readonly detail: string }
  | { readonly kind: "not-armed" }
  | { readonly kind: "refused"; readonly reason: string }
  | { readonly kind: "error"; readonly message: string };

const MESSAGE_URL = "/message";

/** The dashboard's write answer: `{action, status, detail, ok}`. */
interface OutcomeDocument {
  readonly status: string;
  readonly detail: string;
  readonly ok: boolean;
}

/** Narrow an untrusted body to the outcome document, or null when it is not one. */
function parseOutcomeDocument(raw: unknown): OutcomeDocument | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record["status"] !== "string" || typeof record["ok"] !== "boolean") return null;
  return {
    status: record["status"],
    detail: typeof record["detail"] === "string" ? record["detail"] : "",
    ok: record["ok"],
  };
}

/**
 * One plain UI-safe line: the server's own words when they are one short
 * line, a stated fallback otherwise (HTML-shaped or overlong bodies are
 * summarised, never pasted into the UI).
 */
function plainLine(raw: string, fallback: string): string {
  const line = raw.trim();
  return line === "" || line.startsWith("<") || line.length > 140 ? fallback : line;
}

/**
 * Relay one message through the dashboard's operator write-path. The route
 * 404s on an unarmed dashboard (indistinguishable from an unknown path by
 * design) — that reads as "not armed", never as an error to retry. A current
 * dashboard answers the `{action, status, detail, ok}` outcome document, and
 * each status is stated as its own fact — `undelivered` is a 200 whose relay
 * reached nobody, which must never read as "sent". A pre-document dashboard
 * answers plain text; that ladder is kept unchanged beneath the document one.
 */
export async function sendOperatorMessage(
  to: string,
  text: string,
  fetcher: typeof fetch = fetch,
  url: string = MESSAGE_URL,
): Promise<OperatorSendResult> {
  try {
    const response = await fetcher(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, text }),
    });
    // 404 = armed-off by design; 501 = a dashboard from before the
    // write-path existed. Both read as "not armed", never as retryable.
    if (response.status === 404 || response.status === 501) return { kind: "not-armed" };
    const raw = (await response.text()).trim();
    let body: unknown = null;
    try {
      body = JSON.parse(raw) as unknown;
    } catch {
      body = null;
    }
    const document = parseOutcomeDocument(body);
    if (document !== null) {
      const detail = plainLine(document.detail, "");
      if (document.status === "undelivered") return { kind: "undelivered", detail };
      if (document.ok) return { kind: "sent", detail };
      return {
        kind: "refused",
        reason: plainLine(document.detail, `dashboard returned ${response.status}`),
      };
    }
    if (response.ok) return { kind: "sent", detail: "" };
    // The dashboard's refusals are one plain line; anything HTML-shaped or
    // overlong is summarised to its status instead of pasted into the UI.
    return { kind: "refused", reason: plainLine(raw, `dashboard returned ${response.status}`) };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}
