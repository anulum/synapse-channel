// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — historical fleet reconstruction and evidence-bound comparison

import type { JSX } from "react";
import { useMemo } from "react";
import { diffReplayStates } from "../lib/replayDiff";
import type { FleetStateAt } from "../lib/stateAt";
import type { ReplayState } from "../lib/workspace";
import type { CockpitEvent } from "../types";
import { TimeTravelBar } from "./TimeTravelBar";

export interface ReplaySlot {
  readonly seq: number;
  readonly state: FleetStateAt | null;
  readonly note: string | null;
}

interface ReplayWorkbenchProps {
  readonly replay: ReplayState;
  readonly slotA: ReplaySlot | null;
  readonly slotB: ReplaySlot | null;
  readonly events: readonly CockpitEvent[];
  readonly onReplayChange: (replay: ReplayState) => void;
  readonly onReplayReplace: (replay: ReplayState) => void;
  readonly onSelectEvent: (seq: number) => void;
  readonly onSelectTask: (taskId: string) => void;
}

function stampOf(state: FleetStateAt): string {
  if (state.asOfTs === 0) return "timestamp unavailable";
  return new Date(state.asOfTs * 1000).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function slotLabel(name: "A" | "B", slot: ReplaySlot | null): string {
  if (slot === null) return `${name} not selected`;
  if (slot.note !== null) return `${name} unavailable: ${slot.note}`;
  if (slot.state === null) return `${name} seq ${slot.seq} reconstructing`;
  return `${name} seq ${slot.state.asOfSeq} · ${stampOf(slot.state)}`;
}

function maximumSequence(
  replay: ReplayState,
  slotA: ReplaySlot | null,
  slotB: ReplaySlot | null,
  events: readonly CockpitEvent[],
): number {
  let maximum = 1;
  for (const event of events) maximum = Math.max(maximum, event.seq);
  if (replay.mode === "history") maximum = Math.max(maximum, replay.at);
  if (replay.mode === "compare") maximum = Math.max(maximum, replay.a, replay.b);
  maximum = Math.max(maximum, slotA?.state?.logEndSeq ?? 0, slotB?.state?.logEndSeq ?? 0);
  return maximum;
}

function boundedSequence(value: string): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
}

/** Render the replay controls and A-to-B evidence delta without blending live state. */
export function ReplayWorkbench({
  replay,
  slotA,
  slotB,
  events,
  onReplayChange,
  onReplayReplace,
  onSelectEvent,
  onSelectTask,
}: ReplayWorkbenchProps): JSX.Element {
  const maximum = maximumSequence(replay, slotA, slotB, events);
  const diff = useMemo(
    () =>
      replay.mode === "compare" && slotA?.state !== null && slotA?.state !== undefined &&
      slotB?.state !== null && slotB?.state !== undefined
        ? diffReplayStates(slotA.state, slotB.state, events)
        : null,
    [events, replay.mode, slotA, slotB],
  );

  const changeMode = (mode: ReplayState["mode"]): void => {
    if (mode === "live") {
      onReplayChange({ mode });
      return;
    }
    if (mode === "history") {
      const at = replay.mode === "compare" ? replay.b : replay.mode === "history" ? replay.at : maximum;
      onReplayChange({ mode, at });
      return;
    }
    const b = replay.mode === "history" ? replay.at : replay.mode === "compare" ? replay.b : maximum;
    const a = replay.mode === "compare" ? replay.a : Math.max(0, b - 100);
    onReplayChange({ mode, a, b });
  };

  const modeLabel = replay.mode === "live"
    ? "LIVE · claims, board and roster use the current snapshot"
    : replay.mode === "history"
      ? `HISTORY · ${slotLabel("B", slotB)} · claims and board reconstruct B; roster stays live`
      : `COMPARE · ${slotLabel("A", slotA)} → ${slotLabel("B", slotB)} · boards show B; roster stays live`;

  return (
    <section className={`replay replay--${replay.mode}`} aria-label="Fleet replay workbench">
      <TimeTravelBar mode={replay.mode} label={modeLabel} onModeChange={changeMode} />
      {replay.mode !== "live" && (
        <div className="replay__body">
          <div className="replay__positions">
            {replay.mode === "compare" && (
              <label className="replay__position">
                <span>A sequence</span>
                <input
                  type="number"
                  min={0}
                  max={maximum}
                  value={replay.a}
                  onChange={(event) =>
                    onReplayReplace({ mode: "compare", a: boundedSequence(event.target.value), b: replay.b })
                  }
                />
                <input
                  type="range"
                  min={0}
                  max={maximum}
                  value={replay.a}
                  aria-label="Comparison sequence A"
                  onChange={(event) =>
                    onReplayReplace({ mode: "compare", a: boundedSequence(event.target.value), b: replay.b })
                  }
                />
              </label>
            )}
            <label className="replay__position">
              <span>{replay.mode === "compare" ? "B sequence" : "Historical sequence"}</span>
              <input
                type="number"
                min={0}
                max={maximum}
                value={replay.mode === "compare" ? replay.b : replay.at}
                onChange={(event) => {
                  const seq = boundedSequence(event.target.value);
                  onReplayReplace(replay.mode === "compare" ? { mode: "compare", a: replay.a, b: seq } : { mode: "history", at: seq });
                }}
              />
              <input
                type="range"
                min={0}
                max={maximum}
                value={replay.mode === "compare" ? replay.b : replay.at}
                aria-label={replay.mode === "compare" ? "Comparison sequence B" : "Historical sequence"}
                onChange={(event) => {
                  const seq = boundedSequence(event.target.value);
                  onReplayReplace(replay.mode === "compare" ? { mode: "compare", a: replay.a, b: seq } : { mode: "history", at: seq });
                }}
              />
            </label>
            <button
              type="button"
              className="replay__latest"
              onClick={() =>
                onReplayReplace(
                  replay.mode === "compare"
                    ? { mode: "compare", a: replay.a, b: maximum }
                    : { mode: "history", at: maximum },
                )
              }
            >
              latest
            </button>
          </div>
          {slotB?.note !== null && slotB?.note !== undefined && (
            <p className="replay__notice" role="alert">{slotB.note}</p>
          )}
          {replay.mode === "compare" && slotA?.note !== null && slotA?.note !== undefined && (
            <p className="replay__notice" role="alert">{slotA.note}</p>
          )}
          {replay.mode === "compare" && diff !== null && (
            <div className="replay__diff" aria-label={`Replay changes from sequence ${diff.fromSeq} to ${diff.toSeq}`}>
              <div className="replay__summary">
                <span>+{diff.added} added</span>
                <span>−{diff.removed} removed</span>
                <span>~{diff.changed} changed</span>
                <span>{diff.evidenced}/{diff.deltas.length} transition events retained</span>
              </div>
              {diff.deltas.length === 0 ? (
                <p className="replay__empty">No claim or task evidence changed between A and B.</p>
              ) : (
                <ul className="replay__deltas">
                  {diff.deltas.map((delta) => (
                    <li key={`${delta.entity}:${delta.subject}:${delta.change}`} className={`replay-delta replay-delta--${delta.change}`}>
                      <span className="replay-delta__change">{delta.change}</span>
                      <button type="button" className="replay-delta__subject" onClick={() => onSelectTask(delta.subject)}>
                        {delta.entity} · {delta.subject}
                      </button>
                      <span className="replay-delta__summary">{delta.summary}</span>
                      {delta.eventSeq === null ? (
                        <span className="replay-delta__missing">transition event outside retained window</span>
                      ) : (
                        <button type="button" className="replay-delta__event" onClick={() => onSelectEvent(delta.eventSeq as number)}>
                          exact event #{delta.eventSeq}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
