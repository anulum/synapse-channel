// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the approval view of one operation: its intent, both digests, the request, the decision

import type { JSX } from "react";
import { useId, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { MessageKey } from "../lib/i18n";
import {
  approvalView,
  decideCommands,
  MAX_APPROVAL_REASON_BYTES,
  reasonBytes,
  type ApprovalDecideResult,
  type ApprovalDecision,
  type IntentField,
  type OperationChain,
  type OperationsSource,
} from "../lib/operations";

function IntentFields({ fields }: { readonly fields: readonly IntentField[] }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <dl className="op-intent" aria-label={t("operations.approval.intent")}>
      {fields.map((item) => (
        <div key={item.field} className="op-intent__field">
          <dt>{item.field}</dt>
          <dd><code>{item.canonical}</code></dd>
        </div>
      ))}
    </dl>
  );
}

function Digests({ chain, equal }: { readonly chain: OperationChain; readonly equal: boolean }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <div className="op-digests">
      <p className="op-digests__row">
        <span>{t("operations.approval.recorded")}</span> <code>{chain.intentSha256}</code>
      </p>
      <p className="op-digests__row">
        <span>{t("operations.approval.recomputed")}</span> <code>{chain.recomputedSha256}</code>
      </p>
      {equal ? (
        <p className="op-digests__verdict">{t("operations.approval.equal")}</p>
      ) : (
        <p className="op-digests__verdict op-approval__refusal" role="alert">{t("operations.approval.refused")}</p>
      )}
    </div>
  );
}

function CommandLine({ command }: { readonly command: string }): JSX.Element {
  const { t } = useCockpitI18n();
  const [copied, setCopied] = useState<boolean | null>(null);
  const copy = async (): Promise<void> => {
    try {
      if (navigator.clipboard === undefined) throw new Error("no clipboard");
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <li className="op-command">
      <code className="op-command__text">{command}</code>
      <button type="button" className="op-button" onClick={() => void copy()} aria-label={t("operations.approval.copyLabel", { command })}>
        {t("operations.approval.copy")}
      </button>
      {copied !== null && (
        <span className="op-command__status" role="status">
          {copied ? t("operations.approval.copied") : t("operations.approval.copyFailed")}
        </span>
      )}
    </li>
  );
}

const ANSWER_KEYS: Readonly<Record<ApprovalDecideResult["kind"], MessageKey>> = {
  recorded: "operations.approval.decide.recorded",
  refused: "operations.approval.decide.refused",
  "hub-refused": "operations.approval.decide.hubRefused",
  unreachable: "operations.approval.decide.unreachable",
  invalid: "operations.approval.decide.invalid",
  "rate-limited": "operations.approval.decide.rateLimited",
  error: "operations.approval.decide.error",
  "not-armed": "operations.approval.decide.notArmed",
  unauthorised: "operations.approval.decide.unauthorised",
};

/** A decision the dashboard did not record, in words. */
function NotRecorded({ answer }: { readonly answer: ApprovalDecideResult }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <p className="op-approval__refusal" role="alert">
      {t(ANSWER_KEYS[answer.kind], { detail: "detail" in answer ? answer.detail : "" })}
    </p>
  );
}

interface DecideHereProps {
  readonly operationId: string;
  readonly digest: string;
  readonly decide: OperationsSource["decide"];
  readonly onRecorded: (detail: string) => void;
}

/**
 * Decide from the cockpit: the person's reason, approve or reject, then a
 * confirmation that names the digest. The dashboard records the note under the
 * login's relay identity; the view says "recorded" and never that the
 * operation may now run.
 */
function DecideHere({ operationId, digest, decide, onRecorded }: DecideHereProps): JSX.Element {
  const { t } = useCockpitI18n();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<ApprovalDecision | null>(null);
  const [sending, setSending] = useState(false);
  const [answer, setAnswer] = useState<ApprovalDecideResult | null>(null);
  const tooLong = reasonBytes(reason) > MAX_APPROVAL_REASON_BYTES;
  const choose = (decision: ApprovalDecision): void => {
    setAnswer(null);
    setPending(decision);
  };
  const record = async (decision: ApprovalDecision): Promise<void> => {
    setSending(true);
    const result = await decide({ operationId, intentSha256: digest, decision, reason });
    setSending(false);
    setPending(null);
    setAnswer(result);
    if (result.kind === "recorded") onRecorded(result.detail);
  };
  return (
    <div className="op-decide" role="group" aria-label={t("operations.approval.decide.title")}>
      <p className="op-note">{t("operations.approval.decide.who")}</p>
      <label className="op-decide__label" htmlFor={reasonId}>{t("operations.approval.decide.reason")}</label>
      <textarea id={reasonId} className="op-decide__reason" rows={3} value={reason} disabled={sending || pending !== null}
        aria-invalid={tooLong} onChange={(event) => setReason(event.target.value)} />
      <p className={tooLong ? "op-approval__refusal" : "op-note"} role={tooLong ? "alert" : undefined}>
        {t(tooLong ? "operations.approval.decide.tooLong" : "operations.approval.decide.reasonHint", { max: MAX_APPROVAL_REASON_BYTES })}
      </p>
      {pending === null ? (
        <div className="op-decide__actions">
          <button type="button" className="op-button" disabled={tooLong} onClick={() => choose("approved")}>
            {t("operations.approval.decide.approve")}
          </button>
          <button type="button" className="op-button" disabled={tooLong} onClick={() => choose("rejected")}>
            {t("operations.approval.decide.reject")}
          </button>
        </div>
      ) : (
        <div className="op-decide__confirm">
          <p className="op-decide__question">
            {t(pending === "approved" ? "operations.approval.decide.confirmApproved" : "operations.approval.decide.confirmRejected", { digest })}
          </p>
          <button type="button" className="op-button" disabled={sending} onClick={() => void record(pending)}>
            {sending ? t("operations.approval.decide.sending") : t("operations.approval.decide.confirm")}
          </button>
          <button type="button" className="op-button" disabled={sending} onClick={() => setPending(null)}>
            {t("operations.approval.decide.cancel")}
          </button>
        </div>
      )}
      {answer !== null && answer.kind !== "recorded" && <NotRecorded answer={answer} />}
    </div>
  );
}

interface DecideProps {
  readonly operationId: string;
  readonly digest: string;
  readonly reason: string | null;
  readonly decide?: OperationsSource["decide"] | undefined;
  readonly onRecorded: (detail: string) => void;
}

function Decide({ operationId, digest, reason, decide, onRecorded }: DecideProps): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <>
      <figure className="op-words">
        <figcaption>{t("operations.approval.words")}</figcaption>
        <p className="op-words__text">
          {reason === null ? t("operations.approval.noRequest") : reason === "" ? t("operations.approval.noWords") : reason}
        </p>
      </figure>
      {decide !== undefined && <DecideHere operationId={operationId} digest={digest} decide={decide} onRecorded={onRecorded} />}
      <p className="op-note">{t("operations.approval.who")}</p>
      <ul className="op-commands">
        {decideCommands(digest).map((command) => <CommandLine key={command} command={command} />)}
      </ul>
      {decide === undefined && <p className="op-note">{t("operations.approval.notHere")}</p>}
    </>
  );
}

interface OperationApprovalProps {
  readonly chain: OperationChain;
  /** Present only when this login may write: the cockpit then offers to decide here. */
  readonly decide?: OperationsSource["decide"] | undefined;
  /** Called with the dashboard's words once a decision is recorded. */
  readonly onRecorded?: ((detail: string) => void) | undefined;
}

/**
 * For an operation that awaits approval: the intent the declaration kept, field
 * by field in canonical order; the recorded digest beside the one the dashboard
 * recomputed; and, only when they are equal, the requester's own words, the
 * decision here when this login may write, and the commands that decide from a
 * terminal.
 */
export function OperationApproval({ chain, decide, onRecorded }: OperationApprovalProps): JSX.Element | null {
  const { t } = useCockpitI18n();
  const view = approvalView(chain);
  if (view === null) return null;
  return (
    <section className="op-approval" aria-label={t("operations.approval.title")}>
      <p className="op-approval__head">{t("operations.approval.title")}</p>
      {view.kind === "no intent" ? (
        <p className="op-approval__refusal" role="alert">{t("operations.approval.noIntent")}</p>
      ) : (
        <>
          <IntentFields fields={chain.intentFields} />
          <Digests chain={chain} equal={view.kind === "decidable"} />
          {view.kind === "decidable" && (
            <Decide operationId={chain.operationId} digest={view.digest} reason={view.reason} decide={decide}
              onRecorded={onRecorded ?? (() => undefined)} />
          )}
        </>
      )}
    </section>
  );
}
