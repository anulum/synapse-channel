// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the signal log: the plain, legible table under the spine

import { Fragment, memo, useRef, useState } from "react";

import { actorsInWindow, eventsInWindow, windowEdgeLabel, type TimeWindow } from "../lib/brush";
import { buildLogExport, exportFilename } from "../lib/exportLog";
import {
  fetchHistoryWindow,
  fetchLatestSeq,
  HISTORY_WINDOW_SIZE,
  type HistoryWindow,
} from "../lib/history";
import { groupByTask } from "../lib/logGroups";
import { applyQuery, isConstrained, OPEN_QUERY, type LogQuery } from "../lib/logQuery";
import { fetchAndVerify, type VerifyResult } from "../lib/merkleVerify";
import { readLogExportFile, type PostMortem } from "../lib/postmortem";
import { diffWindows, type WindowDiff } from "../lib/windowDiff";
import type { CockpitEvent } from "../types";

/**
 * How many rows the table PAINTS. Measured, not guessed: ten thousand DOM
 * rows cost 9.45 s to paint and 81 MB of heap (2026-07-05 benchmark on the
 * production build); a thousand paint instantly. Everything above the cap
 * is stated as a remainder — an operator narrows a query, they do not read
 * ten thousand rows.
 */
const RENDERED_ROWS_CAP = 1000;

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
          <tr>
            <th>kind</th>
            <th>A</th>
            <th>B</th>
            <th>Δ</th>
          </tr>
        </thead>
        <tbody>
          {diff.kinds.slice(0, 8).map((row) => (
            <tr key={row.kind}>
              <td>{row.kind}</td>
              <td>{row.a}</td>
              <td>{row.b}</td>
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

/** Wall-clock HH:MM:SS for a spine event's timestamp (epoch seconds). */
function timeOf(event: CockpitEvent): string {
  return new Date(event.ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

interface SignalLogProps {
  /** Derived transition events, newest first; the caller owns the cap. */
  readonly events: readonly CockpitEvent[];
  /** The brushed spine window filtering this log, or null for everything. */
  readonly window?: TimeWindow | null;
  /** Clears the brushed window (also clears the spine highlight). */
  readonly onClearWindow?: (() => void) | undefined;
  /** Jump to the causality inspector for the subject a row names. */
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  /**
   * Where the events come from. `hub` = the durable log (real seq + ts; every
   * row can hop by its exact sequence); `derived` = snapshot-diff transitions
   * (local seq, poll-quantised ts; only task-naming rows hop, by task id).
   */
  readonly provenance?: "hub" | "derived";
  /** The operator's query (text/kinds/order), owned by the caller. */
  readonly query?: LogQuery;
  /** Query updates (typing, order toggle, clear). */
  readonly onQueryChange?: ((query: LogQuery) => void) | undefined;
}

function SignalLogView({
  events,
  window = null,
  onClearWindow,
  onSelectTask,
  provenance = "derived",
  query = OPEN_QUERY,
  onQueryChange,
}: SignalLogProps): JSX.Element {
  // Pause freezes the VIEW while the feed keeps recording — the frozen list
  // is a real snapshot of what was on screen, and the header counts what has
  // arrived since, so nothing is silently missed.
  const [paused, setPaused] = useState(false);
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const frozen = useRef<readonly CockpitEvent[]>([]);

  // History scrub: any window of the attested log, picked by sequence. Live
  // rendering continues underneath; leaving history returns to it untouched.
  const [historyOn, setHistoryOn] = useState(false);
  const [historyLatest, setHistoryLatest] = useState(0);
  const [historyPos, setHistoryPos] = useState(0);
  const [historyWindow, setHistoryWindow] = useState<HistoryWindow | null>(null);
  const [historyNote, setHistoryNote] = useState<string | null>(null);
  const scrubTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const enterHistory = async (): Promise<void> => {
    const latest = await fetchLatestSeq();
    if (latest.kind !== "loaded") {
      setHistoryNote(latest.kind === "absent" ? "event feed not served" : latest.message);
      return;
    }
    setHistoryLatest(latest.latest);
    setHistoryPos(latest.latest);
    setHistoryNote(null);
    setHistoryOn(true);
    const window_ = await fetchHistoryWindow(latest.latest);
    if (window_.kind === "loaded") setHistoryWindow(window_.window);
    else setHistoryNote(window_.kind === "absent" ? "event feed not served" : window_.message);
  };

  // Window diffing: pin the shown history window as A, scrub elsewhere, and
  // compare it with the shown window B — two slices of the same attested log.
  const [pinnedWindow, setPinnedWindow] = useState<HistoryWindow | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);

  const leaveHistory = (): void => {
    setHistoryOn(false);
    setHistoryWindow(null);
    setHistoryNote(null);
    setPinnedWindow(null);
    setDiffOpen(false);
  };

  // Post-mortem: the log opened over an exported document instead of the
  // live feed — incident review with no hub attached. The banner states what
  // the document says about itself; malformed files are refused, not fixed.
  const [postMortem, setPostMortem] = useState<{ data: PostMortem; name: string } | null>(null);
  // Per-seq verify verdicts: the proof re-computed in THIS browser.
  const [verdicts, setVerdicts] = useState<ReadonlyMap<number, string>>(new Map());
  const runVerify = (seq: number): void => {
    setVerdicts((current) => new Map(current).set(seq, "verifying…"));
    void fetchAndVerify(seq).then((result: VerifyResult) => {
      const line =
        result.kind === "verified"
          ? `✓ committed to root ${result.root.slice(0, 12)}… (recomputed in this browser)`
          : result.kind === "mismatch"
            ? "✗ proof did not reconstruct the claimed root"
            : result.kind === "absent"
              ? `not in the committed tree: ${result.note}`
              : result.kind === "unserved"
                ? "proof surface not served (/merkle-proof.json)"
                : `verify failed: ${result.message}`;
      setVerdicts((current) => new Map(current).set(seq, line));
    });
  };
  const [postMortemNote, setPostMortemNote] = useState<string | null>(null);
  const filePicker = useRef<HTMLInputElement | null>(null);

  const openExportFile = async (file: File): Promise<void> => {
    const parsed = await readLogExportFile(file);
    if (parsed === null) {
      setPostMortemNote(`${file.name} is not a cockpit export`);
      return;
    }
    setPostMortemNote(null);
    setPostMortem({ data: parsed, name: file.name });
    if (historyOn) leaveHistory();
  };

  const scrubTo = (position: number): void => {
    setHistoryPos(position);
    // Debounce the fetch so dragging the slider costs one request, not fifty.
    if (scrubTimer.current !== undefined) clearTimeout(scrubTimer.current);
    scrubTimer.current = setTimeout(() => {
      void fetchHistoryWindow(position).then((result) => {
        if (result.kind === "loaded") {
          setHistoryWindow(result.window);
          setHistoryNote(null);
        } else {
          setHistoryNote(result.kind === "absent" ? "event feed not served" : result.message);
        }
      });
    }, 250);
  };
  const togglePause = (): void => {
    if (!paused) frozen.current = events;
    setPaused(!paused);
  };
  const base = paused ? frozen.current : events;
  let newerCount = 0;
  if (paused) {
    const frozenHead = frozen.current[0]?.seq;
    if (frozenHead === undefined) {
      newerCount = events.length;
    } else {
      const headAt = events.findIndex((event) => event.seq === frozenHead);
      newerCount = headAt === -1 ? events.length : headAt;
    }
  }

  // Post-mortem beats history beats live. In the file and history modes the
  // text and kind filters still apply; the live brush window does not (those
  // modes are not clock-addressed).
  const shown = postMortem
    ? applyQuery(postMortem.data.events, query)
    : historyOn
      ? applyQuery(historyWindow?.events ?? [], query)
      : applyQuery(eventsInWindow(base, window), query);
  const actors = window === null || historyOn || postMortem !== null ? [] : actorsInWindow(base, window);
  const shownProvenance = postMortem ? postMortem.data.provenance : provenance;

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
          <span className="panel__sub">
            {provenance === "hub" ? "hub event log" : "observed transitions"}
          </span>
        ) : (
          <span className="panel__sub panel__sub--brush">
            {`${windowEdgeLabel(window.fromTs)}–${windowEdgeLabel(window.toTs)} · ${
              actors.length
            } actor${actors.length === 1 ? "" : "s"}`}
            <button type="button" className="panel__clear" onClick={() => onClearWindow?.()}>
              clear
            </button>
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
          onClick={() =>
            onQueryChange?.({ ...query, order: query.order === "newest" ? "oldest" : "newest" })
          }
          title="Toggle render order"
        >
          {query.order === "newest" ? "newest ↓" : "oldest ↑"}
        </button>
        <button
          type="button"
          className="log-controls__toggle"
          onClick={() =>
            onQueryChange?.({ ...query, view: query.view === "flat" ? "compact" : "flat" })
          }
          title="Flat = one row per event; compact = one row per task with its lifecycle"
        >
          {query.view}
        </button>
        {!historyOn && postMortem === null && (
          <button
            type="button"
            className={`log-controls__toggle${paused ? " log-controls__toggle--paused" : ""}`}
            onClick={togglePause}
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
            onClick={() => (historyOn ? leaveHistory() : void enterHistory())}
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
            if (file !== undefined) void openExportFile(file);
          }}
        />
        <button
          type="button"
          className={`log-controls__toggle${postMortem !== null ? " log-controls__toggle--paused" : ""}`}
          onClick={() => {
            if (postMortem !== null) {
              setPostMortem(null);
              setPostMortemNote(null);
            } else filePicker.current?.click();
          }}
          aria-pressed={postMortem !== null}
          title="Open a downloaded cockpit export and review it offline — no hub needed"
        >
          {postMortem !== null ? "close file" : "open"}
        </button>
        {isConstrained(query) && (
          <button
            type="button"
            className="panel__clear"
            onClick={() => onQueryChange?.(OPEN_QUERY)}
            title="Clear the query"
          >
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
      {historyOn && (
        <div className="log-scrub">
          <input
            type="range"
            className="log-scrub__slider"
            min={1}
            max={Math.max(1, historyLatest)}
            value={historyPos}
            onChange={(change) => scrubTo(Number(change.target.value))}
            aria-label="Scrub position in the hub's event log, by sequence"
          />
          <span className="log-scrub__label">
            {historyNote !== null
              ? historyNote
              : historyWindow === null
                ? "fetching…"
                : `seq ${historyWindow.fromSeq}–${historyWindow.toSeq} of ${historyLatest} · window ${HISTORY_WINDOW_SIZE}`}
          </span>
          <button
            type="button"
            className={`log-controls__toggle${pinnedWindow !== null ? " log-controls__toggle--paused" : ""}`}
            onClick={() => {
              if (pinnedWindow !== null) {
                setPinnedWindow(null);
                setDiffOpen(false);
              } else if (historyWindow !== null) setPinnedWindow(historyWindow);
            }}
            disabled={pinnedWindow === null && historyWindow === null}
            title="Pin the shown window as A, then scrub elsewhere and compare"
          >
            {pinnedWindow === null ? "pin A" : `A ${pinnedWindow.fromSeq}–${pinnedWindow.toSeq} ✕`}
          </button>
          <button
            type="button"
            className={`log-controls__toggle${diffOpen ? " log-controls__toggle--paused" : ""}`}
            onClick={() => setDiffOpen(!diffOpen)}
            disabled={pinnedWindow === null || historyWindow === null}
            aria-pressed={diffOpen}
            title="Compare the pinned window A with the shown window B"
          >
            compare
          </button>
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
        ) : query.view === "compact" ? (
          <CompactLogList
            events={shown.slice(0, RENDERED_ROWS_CAP)}
            onSelectTask={onSelectTask}
            provenance={provenance}
            truncated={Math.max(0, shown.length - RENDERED_ROWS_CAP)}
          />
        ) : (
          <table className="log">
            <thead>
              <tr>
                <th scope="col">
                  <span className="visually-hidden">raw event</span>
                </th>
                <th scope="col">time</th>
                <th scope="col">lane</th>
                <th scope="col">kind</th>
                <th scope="col">actor</th>
                <th scope="col">event</th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, RENDERED_ROWS_CAP).map((event) => (
                <Fragment key={event.seq}>
                  <tr className={`log__row log__row--${event.kind}`}>
                    <td className="log__raw">
                      {event.payload !== undefined && (
                        <button
                          type="button"
                          className="log__raw-toggle"
                          aria-expanded={expandedSeq === event.seq}
                          title="Show the hub's raw stored event"
                          onClick={() =>
                            setExpandedSeq(expandedSeq === event.seq ? null : event.seq)
                          }
                        >
                          {"{}"}
                        </button>
                      )}
                    </td>
                    <td className="log__time">{timeOf(event)}</td>
                    <td className="log__lane">{event.lane}</td>
                    <td className="log__kind">
                      <span className="log__dot" aria-hidden="true" />
                      {event.kind}
                    </td>
                    <td className="log__actor" title={event.actor}>
                      {event.actor === "" ? "—" : event.actor}
                    </td>
                    <td className="log__label" title={event.label}>
                      {onSelectTask !== undefined &&
                      ((provenance === "hub" && event.kind !== "chat") || event.taskId !== "") ? (
                        <button
                          type="button"
                          className="log__hop"
                          title={
                            provenance === "hub"
                              ? `Trace the recorded causes of event seq ${event.seq}`
                              : `Trace the recorded causes of ${event.taskId}`
                          }
                          onClick={() =>
                            onSelectTask(provenance === "hub" ? String(event.seq) : event.taskId)
                          }
                        >
                          {event.label}
                        </button>
                      ) : (
                        event.label
                      )}
                    </td>
                  </tr>
                  {expandedSeq === event.seq && event.payload !== undefined && (
                    <tr className="log__detail">
                      <td colSpan={6}>
                        <pre className="log__json">
                          {JSON.stringify(
                            { seq: event.seq, ts: event.ts, kind: event.kind, payload: event.payload },
                            null,
                            2,
                          )}
                        </pre>
                        {shownProvenance === "hub" && (
                          <div className="log__verify">
                            <button
                              type="button"
                              className="log-controls__toggle"
                              onClick={() => runVerify(event.seq)}
                              title="Fetch the RFC 6962 inclusion proof and recompute the root client-side"
                            >
                              verify inclusion
                            </button>
                            {verdicts.has(event.seq) && (
                              <span className="log__verify-verdict">{verdicts.get(event.seq)}</span>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
                          {shown.length > RENDERED_ROWS_CAP && (
                <tr className="log__row">
                  <td colSpan={6} className="log__more">
                    {`+${shown.length - RENDERED_ROWS_CAP} more match — narrow the query to see them`}
                  </td>
                </tr>
              )}
</tbody>
          </table>
        )}
      </div>
    </section>
  );
}

interface CompactLogListProps {
  /** Events beyond the render cap, stated instead of painted. */
  readonly truncated?: number;
  /** Query-filtered events, newest first. */
  readonly events: readonly CockpitEvent[];
  readonly onSelectTask?: ((subject: string) => void) | undefined;
  readonly provenance: "hub" | "derived";
}

/** One row per task with its observed lifecycle inline; chatter stays flat. */
function CompactLogList({ events, onSelectTask, provenance, truncated = 0 }: CompactLogListProps): JSX.Element {
  const compact = groupByTask(events);
  return (
    <div className="log-compact">
      {compact.groups.map((group) => (
        <div key={group.taskId} className="log-group">
          <div className="log-group__head">
            {onSelectTask !== undefined ? (
              <button
                type="button"
                className="log__hop log-group__task"
                title={`Trace the recorded causes of ${group.taskId}`}
                onClick={() => onSelectTask(group.taskId)}
              >
                {group.taskId}
              </button>
            ) : (
              <span className="log-group__task">{group.taskId}</span>
            )}
            {group.lastActor !== "" && (
              <span className="log-group__actor" title={group.lastActor}>
                {group.lastActor}
              </span>
            )}
            <span className="log-group__time">{timeOf(group.events.at(-1) as CockpitEvent)}</span>
          </div>
          <div className="log-group__chain">
            {group.events.map((event) => (
              <span
                key={event.seq}
                className={`log-chip log__row--${event.kind}`}
                title={`${timeOf(event)} · ${event.label}${
                  provenance === "hub" ? ` · seq ${event.seq}` : ""
                }`}
              >
                <span className="log__dot" aria-hidden="true" />
                {event.kind}
              </span>
            ))}
          </div>
        </div>
      ))}
      {compact.ungrouped.length > 0 && (
        <div className="log-group log-group--chatter">
          <div className="log-group__head">
            <span className="log-group__task">chatter · {compact.ungrouped.length}</span>
          </div>
          <div className="log-group__chain">
            {compact.ungrouped.slice(0, 40).map((event) => (
              <span
                key={event.seq}
                className={`log-chip log__row--${event.kind}`}
                title={`${timeOf(event)} · ${event.actor === "" ? "" : `${event.actor} · `}${event.label}`}
              >
                <span className="log__dot" aria-hidden="true" />
                {event.kind}
              </span>
            ))}
            {compact.ungrouped.length > 40 && (
              <span className="log-chip log-chip--more">{`+${compact.ungrouped.length - 40}`}</span>
            )}
          </div>
        </div>
      )}
      {truncated > 0 && (
        <div className="log__more">
          {`+${truncated} more match beyond the render cap — narrow the query to see them`}
        </div>
      )}
    </div>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const SignalLog = memo(SignalLogView);
