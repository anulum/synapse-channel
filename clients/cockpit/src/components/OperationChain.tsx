// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — one governed operation's chain, link by link, each with its evidence

import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { MessageKey } from "../lib/i18n";
import type { VerifyResult } from "../lib/merkleVerify";
import {
  instantText,
  phaseOf,
  readState,
  type ChainLink,
  type ChainResult,
  type OperationChain as Chain,
  type OperationsSource,
} from "../lib/operations";
import type { CockpitScope } from "../lib/scope";
import { OperationApproval } from "./OperationApproval";

type Translate = ReturnType<typeof useCockpitI18n>["t"];

const LINK_LABELS: Readonly<Record<string, MessageKey>> = {
  declared: "operations.link.declared",
  approval: "operations.link.approval",
  answer: "operations.link.answer",
  seal: "operations.link.seal",
  outcome: "operations.link.outcome",
  verification: "operations.link.verification",
};

const DIGEST_CHECKS: Readonly<Record<string, MessageKey>> = {
  matches: "operations.digest.matches",
  differs: "operations.digest.differs",
  "no intent": "operations.digest.noIntent",
};

/** The state as the hub gave it, drawn by its kind, with what an unknown or silent state means. */
export function OperationState({ state }: { readonly state: string }): JSX.Element {
  const { t } = useCockpitI18n();
  const reading = readState(state);
  return (
    <span className={`op-state op-state--${reading.tone}`}>
      <span className="op-state__text">{reading.text === "" ? t("operations.notRecorded") : reading.text}</span>
      {!reading.known && <> <span className="op-state__note">{t("operations.state.unrecognised")}</span></>}
    </span>
  );
}

function verdictText(t: Translate, seq: number, result: VerifyResult): string {
  if (result.kind === "verified") return t("operations.evidence.verified", { seq, root: result.root });
  if (result.kind === "mismatch") return t("operations.evidence.mismatch");
  if (result.kind === "absent") return t("operations.evidence.absent", { note: result.note });
  if (result.kind === "unserved") return t("operations.evidence.unserved");
  return t("operations.evidence.error", { error: result.message });
}

type VerifyRow = OperationsSource["verifyRow"];

/** One link's "open evidence": its row's inclusion proof, checked in the browser. */
function LinkEvidence({ seq, verifyRow }: { readonly seq: number; readonly verifyRow: VerifyRow }): JSX.Element {
  const { t } = useCockpitI18n();
  const [verdict, setVerdict] = useState<{ readonly tone: string; readonly text: string } | null>(null);
  const open = (): void => {
    setVerdict({ tone: "checking", text: t("operations.evidence.checking") });
    void verifyRow(seq).then((result) => setVerdict({ tone: result.kind, text: verdictText(t, seq, result) }));
  };
  return (
    <span className="op-link__evidence">
      <button type="button" className="op-button" onClick={open} disabled={verdict?.tone === "checking"}>
        {t("operations.link.evidence", { seq })}
      </button>
      {verdict !== null && (
        <span className={`op-link__verdict op-link__verdict--${verdict.tone}`} role="status">
          {verdict.text}
        </span>
      )}
    </span>
  );
}

function LinkItem({ link, verifyRow }: { readonly link: ChainLink; readonly verifyRow: VerifyRow }): JSX.Element {
  const { t } = useCockpitI18n();
  const label = LINK_LABELS[link.kind];
  const notRecorded = t("operations.notRecorded");
  return (
    <li className="op-link">
      <span className="op-link__phase" aria-hidden="true">{phaseOf(link.kind)}</span>
      <div className="op-link__body">
        <span className="op-link__meta">
          <span className="op-link__kind">{label === undefined ? link.kind : t(label)}</span>
          <span className="op-link__seq">{t("operations.link.seq", { seq: link.seq })}</span>
          <span className="op-link__time">{instantText(link.at) ?? notRecorded}</span>
          <span className="op-link__actor">{t("operations.link.by", { actor: link.actor || notRecorded })}</span>
        </span>
        {link.detail.length > 0 && (
          <dl className="op-link__detail">
            {link.detail.map(([key, value]) => (
              <div key={key} className="op-link__field">
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
        <LinkEvidence seq={link.seq} verifyRow={verifyRow} />
      </div>
    </li>
  );
}

function ChainHints({ state }: { readonly state: string }): JSX.Element | null {
  const { t } = useCockpitI18n();
  if (state === "attempt open, silent") return <p className="op-hint">{t("operations.hint.silent")}</p>;
  if (state === "outcome reported, unknown") return <p className="op-hint">{t("operations.hint.unknown")}</p>;
  return null;
}

function ChainBody({ chain, verifyRow }: { readonly chain: Chain; readonly verifyRow: VerifyRow }): JSX.Element {
  const { t } = useCockpitI18n();
  if (!chain.present) {
    return <p className="panel__placeholder">{t("operations.chain.absent", { operation: chain.operationId })}</p>;
  }
  const digest = DIGEST_CHECKS[chain.digestCheck];
  return (
    <>
      <div className="op-chain__head">
        <span className="op-chain__id">{chain.operationId}</span>
        <OperationState state={chain.state} />
        <span className="op-chain__fact">{t("operations.row.task", { task: chain.taskId || t("operations.notRecorded") })}</span>
        <span className={`op-chain__fact op-digest op-digest--${chain.digestCheck === "differs" ? "differs" : "plain"}`}>
          {chain.intentSha256 === "" ? t("operations.chain.noIntent") : t("operations.chain.intent", { digest: chain.intentSha256 })}
          {" · "}
          {digest === undefined ? chain.digestCheck : t(digest)}
        </span>
      </div>
      <ChainHints state={chain.state} />
      <OperationApproval chain={chain} />
      {chain.scope !== null && !chain.scope.applied && <p className="op-note">{t("operations.chain.scopeWhole")}</p>}
      {chain.attempts.length > 0 && (
        <ul className="op-attempts" aria-label={t("operations.chain.attempts")}>
          {chain.attempts.map((attempt) => (
            <li key={attempt.attemptId} className="op-attempt">
              <span>{t("operations.chain.attempt", { attempt: attempt.attemptId, executor: attempt.executor || t("operations.notRecorded") })}</span>
              <OperationState state={attempt.state} />
            </li>
          ))}
        </ul>
      )}
      <ol className="op-links" aria-label={t("operations.chain.links")}>
        {chain.chain.map((link) => <LinkItem key={link.seq} link={link} verifyRow={verifyRow} />)}
      </ol>
      {chain.unreadable > 0 && <p className="op-note">{t("operations.unreadable", { count: chain.unreadable })}</p>}
      <p className="op-chain__foot">
        <span>{t("operations.chain.asOf", { seq: chain.asOfSeq ?? t("operations.notRecorded") })}</span>
        <span>{t("operations.chain.custody", { custody: chain.custody || t("operations.notRecorded") })}</span>
      </p>
    </>
  );
}

interface OperationChainProps {
  readonly operationId: string;
  readonly scope: CockpitScope;
  readonly source: OperationsSource;
}

/** Read one operation's chain when it is chosen, and show every link the report gives. */
export function OperationChain({ operationId, scope, source }: OperationChainProps): JSX.Element {
  const { t } = useCockpitI18n();
  const [result, setResult] = useState<{ readonly id: string; readonly value: ChainResult } | null>(null);
  const section = useRef<HTMLElement | null>(null);
  // In a narrow inspector the chain sits below the list: bring the chosen one into view.
  useEffect(() => {
    section.current?.scrollIntoView?.({ block: "nearest" });
  }, [operationId, result]);
  useEffect(() => {
    let live = true;
    void source.readChain(operationId, scope).then((value) => {
      if (live) setResult({ id: operationId, value });
    });
    return () => {
      live = false;
    };
  }, [operationId, scope, source]);
  const current = result !== null && result.id === operationId ? result.value : null;
  return (
    <section ref={section} className="op-chain" aria-label={t("operations.chain.aria", { operation: operationId })}>
      {current === null ? (
        <p className="panel__placeholder" role="status">{t("operations.chain.loading")}</p>
      ) : current.kind === "absent" ? (
        <p className="panel__placeholder">{t("operations.absent")}</p>
      ) : current.kind === "error" ? (
        <p className="panel__placeholder">{t("operations.chain.error", { error: current.message })}</p>
      ) : (
        <ChainBody chain={current.chain} verifyRow={source.verifyRow} />
      )}
    </section>
  );
}
