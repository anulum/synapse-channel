// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the operations tab: the declared operations by state, and one chain

import type { JSX } from "react";
import { useContext, useEffect, useMemo, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { FeedState } from "../lib/feed";
import {
  groupByState,
  instantText,
  OPERATIONS_CONNECTING,
  type OperationRow,
  type OperationsPage,
  type OperationsSource,
} from "../lib/operations";
import { parseScope, scopeText } from "../lib/scope";
import { OperationChain, OperationState } from "./OperationChain";
import { ScopeContext, ScopeTag } from "./ScopeTag";
import "./OperationsView.css";

interface RowProps {
  readonly row: OperationRow;
  readonly selected: boolean;
  readonly onSelect: (operationId: string) => void;
}

function OperationItem({ row, selected, onSelect }: RowProps): JSX.Element {
  const { t } = useCockpitI18n();
  const notRecorded = t("operations.notRecorded");
  return (
    <li>
      <button type="button" className="op-row" aria-pressed={selected} onClick={() => onSelect(row.operationId)}>
        <span className="op-row__id">{row.operationId}</span>{" "}
        <span className="op-row__fact">{t("operations.row.task", { task: row.taskId || notRecorded })}</span>{" "}
        <span className="op-row__fact">{t("operations.row.declarer", { who: row.declarer || notRecorded })}</span>{" "}
        <span className="op-row__fact">
          {t("operations.row.latest", { kind: row.latestKind || notRecorded, seq: row.latestSeq ?? notRecorded })}
          {" · "}
          {instantText(row.latestAt) ?? notRecorded}
        </span>
      </button>
    </li>
  );
}

interface ListProps {
  readonly page: OperationsPage;
  readonly selected: string | null;
  readonly onSelect: (operationId: string) => void;
}

function OperationGroups({ page, selected, onSelect }: ListProps): JSX.Element {
  const { t } = useCockpitI18n();
  if (page.operations.length === 0) {
    return (
      <p className="panel__placeholder">
        {page.scope?.applied === true ? t("operations.emptyScope") : t("operations.empty")}
      </p>
    );
  }
  return (
    <div className="op-groups">
      {groupByState(page.operations).map((group, index) => (
        <div key={group.state.text} className="op-group">
          <span className="op-group__head" id={`op-group-${index}`}>
            <OperationState state={group.state.text} />{" "}
            <span className="op-group__count">{group.rows.length}</span>
          </span>
          <ul className="op-group__rows" aria-labelledby={`op-group-${index}`}>
            {group.rows.map((row) => (
              <OperationItem key={row.operationId} row={row} selected={row.operationId === selected} onSelect={onSelect} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function FeedStatus({ state }: { readonly state: FeedState<OperationsPage> }): JSX.Element | null {
  const { t } = useCockpitI18n();
  if (state.status === "connecting") return <p className="panel__placeholder" role="status">{t("operations.connecting")}</p>;
  if (state.status === "absent") return <p className="panel__placeholder">{t("operations.absent")}</p>;
  if (state.status === "error") return <p className="op-note" role="status">{t("operations.error", { error: state.error ?? "" })}</p>;
  return null;
}

interface PageProps extends ListProps {
  /** The cursor this page was read before, or null for the newest page. */
  readonly before: number | null;
  readonly onCursor: (before: number | null) => void;
}

function OperationsPageView({ page, before, selected, onSelect, onCursor }: PageProps): JSX.Element {
  const { t } = useCockpitI18n();
  const notRecorded = t("operations.notRecorded");
  return (
    <>
      <p className="op-note">
        {t("operations.page", {
          shown: page.operations.length,
          total: page.total ?? notRecorded,
          seq: page.asOfSeq ?? notRecorded,
        })}
      </p>
      {page.unreadable > 0 && <p className="op-note">{t("operations.unreadable", { count: page.unreadable })}</p>}
      <OperationGroups page={page} selected={selected} onSelect={onSelect} />
      <div className="op-pager">
        {before !== null && (
          <button type="button" className="op-button" onClick={() => onCursor(null)}>
            {t("operations.newest")}
          </button>
        )}
        {page.nextBefore !== null && (
          <button type="button" className="op-button" onClick={() => onCursor(page.nextBefore)}>
            {t("operations.older")}
          </button>
        )}
      </div>
    </>
  );
}

/**
 * The declared operations of this hub's store, grouped by state, and the chosen
 * one's chain. The reads come from `source`, which the always-loaded inspector
 * owns, so this chunk reaches no network code of its own.
 */
interface OperationsViewProps {
  readonly source: OperationsSource;
  /** The chosen operation when the workspace owns the choice; the tab keeps its own otherwise. */
  readonly selected?: string | null;
  readonly onSelect?: ((operationId: string | null) => void) | undefined;
  /** Whether this login may write: the approval view then offers to decide here. */
  readonly canDecide?: boolean;
}

export function OperationsView({ source, selected: chosen, onSelect, canDecide = false }: OperationsViewProps): JSX.Element {
  const { t } = useCockpitI18n();
  const scopeKey = scopeText(useContext(ScopeContext));
  const scope = useMemo(() => parseScope(scopeKey), [scopeKey]);
  const [cursor, setCursor] = useState<{ readonly scope: string; readonly before: number | null }>({ scope: scopeKey, before: null });
  const before = cursor.scope === scopeKey ? cursor.before : null;
  const [state, setState] = useState<FeedState<OperationsPage>>(OPERATIONS_CONNECTING);
  const [own, setOwn] = useState<string | null>(null);
  const selected = onSelect === undefined ? own : (chosen ?? null);
  const setSelected = onSelect ?? setOwn;
  useEffect(() => {
    setState(OPERATIONS_CONNECTING);
    const feed = source.openList(scope, before);
    const unsubscribe = feed.subscribe(setState);
    return () => {
      unsubscribe();
      feed.stop();
    };
  }, [scope, before, source]);
  return (
    <section className="panel ops" aria-label={t("operations.aria")}>
      <div className="panel__head">
        <span>{t("operations.title")}</span>
        <ScopeTag />
        <span className="panel__sub">{t("operations.sub")}</span>
      </div>
      <div className="panel__body ops__body">
        <div className="ops__list">
          <FeedStatus state={state} />
          {state.data !== null && (
            <OperationsPageView page={state.data} before={before} selected={selected} onSelect={setSelected}
              onCursor={(next) => setCursor({ scope: scopeKey, before: next })} />
          )}
        </div>
        {selected === null ? (
          <p className="panel__placeholder ops__pick">{t("operations.chain.pick")}</p>
        ) : (
          <OperationChain operationId={selected} scope={scope} source={source} canDecide={canDecide} />
        )}
      </div>
    </section>
  );
}
