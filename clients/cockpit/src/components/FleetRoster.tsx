// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the fleet roster panel: who is present and what they hold

import type { JSX } from "react";
import { memo } from "react";

import { Ident } from "./Ident";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { RosterEntry, RowStatus } from "../lib/roster";
import type { SeatState } from "../types";
import { identityMatchesSelection } from "../lib/selection";
import type { CockpitSelection } from "../lib/workspace";

/** Glyph per status — redundant with colour and row order, never colour alone. */
const STATUS_GLYPH: Record<RowStatus, string> = {
  conflict: "▲",
  stale: "!",
  holding: "●",
  idle: "·",
};

/** Short status word for the row's accessible label and tooltip. */
/** What the hub's "deaf" means, as `synapse who` and the glossary say it. */
const DEAF_TITLE =
  "The hub would warn a sender: no live waiter, and no reaction within its window.";

/** What each seat state says, and what it does not. */
const SEAT_TITLE: Record<Exclude<SeatState, "listening">, string> = {
  "active, not listening": "Working by the record (a held claim, or a claim or note in the last 15 minutes) with no socket receiving its messages.",
  silent: "Nothing recent in the record and no socket receiving its messages.",
};

const STATUS_WORD: Record<RowStatus, string> = {
  conflict: "branch conflict",
  stale: "stale lease",
  holding: "holding claims",
  idle: "idle",
};

/** How many held paths a row shows before collapsing the tail into a count. */
const PATHS_SHOWN = 3;

function lastSegment(name: string): string {
  const slash = name.lastIndexOf("/");
  return slash === -1 ? name : name.slice(slash + 1);
}

function project(name: string): string {
  const slash = name.indexOf("/");
  return slash === -1 ? "" : name.slice(0, slash);
}

interface RosterRowProps {
  readonly entry: RosterEntry;
  /** Opens the agent detail drawer. */
  readonly onInspect?: ((name: string) => void) | undefined;
  readonly selected: boolean;
}

function RosterRow({ entry, onInspect, selected }: RosterRowProps): JSX.Element {
  const held = entry.paths.length;
  const shown = entry.paths.slice(0, PATHS_SHOWN);
  const overflow = held - shown.length;
  const claimCount = entry.activeClaims.length + entry.staleClaims.length;
  const identity = <>
    <Ident className="roster-row__name" value={lastSegment(entry.agent)} />
    {project(entry.agent) !== "" && <span className="roster-row__project">{project(entry.agent)}</span>}
  </>;

  return (
    <li
      className={`roster-row roster-row--${entry.status}${entry.seat === "listening" ? "" : " roster-row--not-listening"}${onInspect !== undefined ? " roster-row--link" : ""}${selected ? " context-match" : ""}`}
      aria-current={selected ? "true" : undefined}
      title={`${entry.agent} — ${STATUS_WORD[entry.status]} · ${entry.seat}`}
    >
      <span className="roster-row__glyph" aria-hidden="true">
        {STATUS_GLYPH[entry.status]}
      </span>
      {onInspect === undefined ? <span className="roster-row__id">{identity}</span> : (
        <button
          type="button"
          className="roster-row__id roster-row__inspect"
          aria-label={`Inspect agent ${entry.agent}`}
          aria-haspopup="dialog"
          onClick={() => onInspect(entry.agent)}
        >{identity}</button>
      )}
      <span className="roster-row__meta">
        {claimCount > 0 ? (
          <span className="roster-row__count">{`${claimCount} claim${claimCount === 1 ? "" : "s"}`}</span>
        ) : (
          <span className="roster-row__count roster-row__count--none">no claims</span>
        )}
        {entry.roles.map((role) => (
          <span
            key={role}
            className="roster-row__tag roster-row__tag--role"
            title={`answers to the role '${role}'`}
          >
            {role}
          </span>
        ))}
        {entry.seat !== "listening" && (
          <span
            className={`roster-row__tag${entry.seat === "silent" ? "" : " roster-row__tag--warn"}`}
            title={SEAT_TITLE[entry.seat]}
          >
            {entry.seat}
          </span>
        )}
        {entry.deaf !== null && (
          <span className="roster-row__tag roster-row__tag--warn" title={DEAF_TITLE}>
            deaf
          </span>
        )}
      </span>
      {held > 0 && (
        <ul className="roster-row__paths">
          {shown.map((path) => (
            <li key={path} className="roster-row__path" title={path}>
              {path}
            </li>
          ))}
          {overflow > 0 && <li className="roster-row__path roster-row__path--more">{`+${overflow} more`}</li>}
        </ul>
      )}
    </li>
  );
}

interface FleetRosterProps {
  readonly roster: readonly RosterEntry[];
  /** Number of `-rx` waiters folded out of the primary rows, if any. */
  readonly waiters: number;
  /** Presence holders the headline counts; they never get a row. */
  readonly presenceHolders?: number;
  /** Opens the agent detail drawer. */
  readonly onInspect?: ((name: string) => void) | undefined;
  /** Shared cockpit entity selected in the URL. */
  readonly selection?: CockpitSelection | null;
}

function FleetRosterView({
  roster,
  waiters,
  presenceHolders = 0,
  onInspect,
  selection = null,
}: FleetRosterProps): JSX.Element {
  const { t } = useCockpitI18n();
  const live = roster.filter((entry) => entry.seat === "listening").length;

  return (
    <section className="panel" aria-label="Fleet roster">
      <div className="panel__head">
        <span>Fleet roster</span>
        <span className="panel__count">{live}</span>
        {waiters > 0 && <span className="panel__sub">{`${waiters} waiting`}</span>}
        {presenceHolders > 0 && (
          <span className="panel__sub" title={t("roster.presenceTitle")}>
            {t("roster.presenceHolders", { count: presenceHolders })}
          </span>
        )}
      </div>
      <div className="panel__body" tabIndex={0}>
        {roster.length === 0 ? (
          <p className="panel__placeholder">No agents present. Waiting for the hub.</p>
        ) : (
          <ul className="roster">
            {roster.map((entry) => (
              <RosterRow
                key={entry.agent}
                entry={entry}
                onInspect={onInspect}
                selected={identityMatchesSelection(entry.agent, selection)}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const FleetRoster = memo(FleetRosterView);
