// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log: controls and workspace composition

import type { JSX } from "react";
import { memo, useRef } from "react";

import { windowEdgeLabel, type TimeWindow } from "../lib/brush";
import { buildLogExport, exportFilename } from "../lib/exportLog";
import { HISTORY_WINDOW_SIZE } from "../lib/history";
import { isConstrained, OPEN_QUERY, type LogQuery } from "../lib/logQuery";
import { diffWindows, type WindowDiff } from "../lib/windowDiff";
import type { CockpitSelection } from "../lib/workspace";
import { useSignalLogWorkspace } from "../hooks/useSignalLogWorkspace";
import type { CockpitEvent } from "../types";
import { SignalLogRows } from "./SignalLogRows";

function rateLabel(rate: number | null): string {
  return rate === null ? "—" : `${rate.toFixed(1)}/min`;
}

/** The A↔B comparison strip: arithmetic over attested counts, no judgement. */
function WindowDiffView({
  diff,
  labelA,
  labelB,
}: {
  readonly diff: WindowDiff;
  readonly labelA: string;
  readonly labelB: string;
}): JSX.Element {
  return (
    <div className="log-diff">
      <div className="log-diff__totals">
        <span>{`A ${labelA} · ${diff.totalA} events · ${rateLabel(diff.rateA)}`}</span>
        <span>{`B ${labelB} · ${diff.totalB} events · ${rateLabel(diff.rateB)}`}</span>
      </div>
      <table className="log-diff__table">
        <thead>
          <tr><th>kind</th><th>A</th><th>B</th><th>Δ</th></tr>
        </thead>
        <tbody>
          {diff.kinds.slice(0, 8).map((row) => (
            <tr key={row.kind}>
              <td>{row.kind}</td><td>{row.a}</td><td>{row.b}</td>
              <td className={row.delta > 0 ? "log-diff__up" : row.delta < 0 ? "log-diff__down" : ""}>
                {row.delta > 0 ? `+${row.delta}` : row.delta}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(diff.appeared.length > 0 || diff.wentQuiet.length > 0) && (
        <div className="log-diff__actors">
          {diff.appeared.length > 0 && <span>{`appeared: ${diff.appeared.join(", ")}`}</span>}
          {diff.wentQuiet.length > 0 && <span>{`went quiet: ${diff.wentQuiet.join(", ")}`}</span>}
        </div>
      )}
    </div>
  );
}

/** Hand the shown window to the operator as a self-describing JSON download. */
function downloadShown(
  events: readonly CockpitEvent[],
  provenance: "hub" | "derived",
  query: LogQuery,
  window: TimeWindow | null,
): void {
  const nowMs = Date.now();
  const document_ = buildLogExport(events, provenance, query, window, nowMs);
  const blob = new Blob([JSON.stringify(document_, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = exportFilename(provenance, nowMs);
  anchor.click();
  URL.revokeObjectURL(url);
}

interface SignalLogProps {
  readonly events: readonly CockpitEvent[];
  readonly window?: TimeWindow | null;
  readonly onClearWindow?: (() => void) | undefined;
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  readonly provenance?: "hub" | "derived";
  readonly query?: LogQuery;
  readonly onQueryChange?: ((query: LogQuery) => void) | undefined;
  readonly selection?: CockpitSelection | null;
  readonly onSelectEvent?: ((seq: number) => void) | undefined;
}

function SignalLogView({
  events,
  window = null,
  onClearWindow,
  onSelectTask,
  provenance = "derived",
  query = OPEN_QUERY,
  onQueryChange,
  selection = null,
  onSelectEvent,
}: SignalLogProps): JSX.Element {
  const filePicker = useRef<HTMLInputElement | null>(null);
  const workspace = useSignalLogWorkspace({ events, window, query, provenance });
  const {
    paused,
    newerCount,
    historyOn,
    historyLatest,
    historyPos,
    historyWindow,
    historyNote,
    pinnedWindow,
    diffOpen,
    postMortem,
    postMortemNote,
    shown,
    actors,
    shownProvenance,
  } = workspace;

  return (
    <section className="panel" aria-label="Signal log">
      <div className="panel__head">
        <span>Signal log</span>
        <span className="panel__count">{shown.length}</span>
        {postMortem !== null ? (
          <span className="panel__sub">
            {postMortem.data.provenance === "hub" ? "hub event log · file" : "observed transitions · file"}
          </span>
        ) : window === null ? (
          <span className="panel__sub">{provenance === "hub" ? "hub event log" : "observed transitions"}</span>
        ) : (
          <span className="panel__sub panel__sub--brush">
            {`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)} · ${actors.length} actor${actors.length === 1 ? "" : "s"}`}
            <button type="button" className="panel__clear" onClick={() => onClearWindow?.()}>clear</button>
          </span>
        )}
      </div>

      <div className="log-controls">
        <input
          className="log-controls__search"
          value={query.text}
          onChange={(change) => onQueryChange?.({ ...query, text: change.target.value })}
          placeholder="search events (actor, task, text)"
          aria-label="Search events by actor, task, or text"
        />
        <button
          type="button"
          className="log-controls__toggle"
          onClick={() => onQueryChange?.({ ...query, order: query.order === "newest" ? "oldest" : "newest" })}
          title="Toggle render order"
        >
          {query.order === "newest" ? "newest ↓" : "oldest ↑"}
        </button>
        <button
          type="button"
          className="log-controls__toggle"
          onClick={() => onQueryChange?.({ ...query, view: query.view === "flat" ? "compact" : "flat" })}
          title="Flat = one row per event; compact = one row per task with its lifecycle"
        >
          {query.view}
        </button>
        {!historyOn && postMortem === null && (
          <button
            type="button"
            className={`log-controls__toggle${paused ? " log-controls__toggle--paused" : ""}`}
            onClick={workspace.togglePause}
            aria-pressed={paused}
            title="Freeze the view while the feed keeps recording"
          >
            {paused ? `paused · ${newerCount} new` : "pause"}
          </button>
        )}
        {postMortem === null && (
          <button
            type="button"
            className={`log-controls__toggle${historyOn ? " log-controls__toggle--paused" : ""}`}
            onClick={() => historyOn ? workspace.leaveHistory() : void workspace.enterHistory()}
            aria-pressed={historyOn}
            disabled={provenance !== "hub"}
            title={
              provenance === "hub"
                ? "Scrub any window of the hub's durable log"
                : "History needs the hub-attested event feed (--feeds-db)"
            }
          >
            {historyOn ? "live" : "history"}
          </button>
        )}
        <button
          type="button"
          className="log-controls__toggle"
          onClick={() => downloadShown(shown, shownProvenance, query, window)}
          disabled={shown.length === 0}
          title="Download the shown events as JSON (provenance and query stated in the document)"
        >
          export
        </button>
        <input
          ref={filePicker}
          type="file"
          accept="application/json,.json"
          className="log-controls__file"
          aria-label="Open a cockpit export for post-mortem review"
          onChange={(change) => {
            const file = change.target.files?.[0];
            change.target.value = "";
            if (file !== undefined) void workspace.openExportFile(file);
          }}
        />
        <button
          type="button"
          className={`log-controls__toggle${postMortem !== null ? " log-controls__toggle--paused" : ""}`}
          onClick={() => postMortem !== null ? workspace.closePostMortem() : filePicker.current?.click()}
          aria-pressed={postMortem !== null}
          title="Open a downloaded cockpit export and review it offline — no hub needed"
        >
          {postMortem !== null ? "close file" : "open"}
        </button>
        {isConstrained(query) && (
          <button type="button" className="panel__clear" onClick={() => onQueryChange?.(OPEN_QUERY)} title="Clear the query">
            reset
          </button>
        )}
      </div>

      {(postMortem !== null || postMortemNote !== null) && (
        <div className="log-scrub log-scrub--file">
          <span className="log-scrub__label">
            {postMortemNote !== null
              ? postMortemNote
              : postMortem !== null
                ? `post-mortem · ${postMortem.name} · ${postMortem.data.count} events · exported ${postMortem.data.exportedAt || "(unstamped)"}`
                : ""}
          </span>
        </div>
      )}

      {(historyOn || historyNote !== null) && (
        <div className="log-scrub">
          {historyOn && (
            <input
              type="range"
              className="log-scrub__slider"
              min={1}
              max={Math.max(1, historyLatest)}
              value={historyPos}
              onChange={(change) => workspace.scrubTo(Number(change.target.value))}
              aria-label="Scrub position in the hub's event log, by sequence"
            />
          )}
          <span className="log-scrub__label">
            {historyNote !== null
              ? historyNote
              : historyWindow === null
                ? "fetching…"
                : `seq ${historyWindow.fromSeq}–${historyWindow.toSeq} of ${historyLatest} · window ${HISTORY_WINDOW_SIZE}`}
          </span>
          {historyOn && (
            <>
              <button
                type="button"
                className={`log-controls__toggle${pinnedWindow !== null ? " log-controls__toggle--paused" : ""}`}
                onClick={workspace.togglePinnedWindow}
                disabled={pinnedWindow === null && historyWindow === null}
                title="Pin the shown window as A, then scrub elsewhere and compare"
              >
                {pinnedWindow === null ? "pin A" : `A ${pinnedWindow.fromSeq}–${pinnedWindow.toSeq} ✕`}
              </button>
              <button
                type="button"
                className={`log-controls__toggle${diffOpen ? " log-controls__toggle--paused" : ""}`}
                onClick={workspace.toggleDiff}
                disabled={pinnedWindow === null || historyWindow === null}
                aria-pressed={diffOpen}
                title="Compare the pinned window A with the shown window B"
              >
                compare
              </button>
            </>
          )}
        </div>
      )}

      {historyOn && diffOpen && pinnedWindow !== null && historyWindow !== null && (
        <WindowDiffView
          diff={diffWindows(pinnedWindow.events, historyWindow.events)}
          labelA={`seq ${pinnedWindow.fromSeq}–${pinnedWindow.toSeq}`}
          labelB={`seq ${historyWindow.fromSeq}–${historyWindow.toSeq}`}
        />
      )}

      <div className="panel__body panel__body--flush">
        {shown.length === 0 ? (
          <p className="panel__placeholder panel__placeholder--padded">
            {isConstrained(query)
              ? "No events match the query."
              : window === null
                ? "No coordination events observed yet. The spine baseline stays flat until the fleet moves."
                : "No observed events inside the brushed window."}
          </p>
        ) : (
          <SignalLogRows
            events={shown}
            view={query.view}
            navigationProvenance={provenance}
            evidenceProvenance={shownProvenance}
            onSelectTask={onSelectTask}
            onSelectEvent={onSelectEvent}
            selection={selection}
          />
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const SignalLog = memo(SignalLogView);
