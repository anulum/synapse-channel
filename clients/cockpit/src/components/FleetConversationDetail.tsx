// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — selected fleet identity, project, and conversation evidence

import type { FormEvent, JSX } from "react";
import { useMemo, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import type { MessageKey, CockpitLocale } from "../lib/i18n";
import type {
  CommunicationEdge,
  CommunicationNode,
  ConversationMessage,
  ProjectTraffic,
} from "../lib/communications";
import { conversationEvidenceFor } from "../lib/conversationEvidence";
import type {
  MessageResponseInput,
  OperatorActionResult,
  SemanticResponseStatus,
} from "../lib/operatorActions";

type Translator = (
  key: MessageKey,
  values?: Readonly<Record<string, string | number>>,
) => string;

function timeAgo(ts: number, t: Translator): string {
  if (ts <= 0) return t("fleet.relative.quiet");
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return t("fleet.relative.seconds", { count: seconds });
  if (seconds < 3600) return t("fleet.relative.minutes", { count: Math.floor(seconds / 60) });
  return t("fleet.relative.hours", { count: Math.floor(seconds / 3600) });
}

function countLabel(
  count: number,
  singular: MessageKey,
  plural: MessageKey,
  t: Translator,
): string {
  return `${count} ${t(count === 1 ? singular : plural)}`;
}

export function FleetNodeDetail({
  node,
  canMessage,
  onMessagePeer,
}: {
  readonly node: CommunicationNode;
  readonly canMessage: boolean;
  readonly onMessagePeer?: ((identity: string) => void) | undefined;
}): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <aside className="fleet-selection" aria-label={t("fleet.detail.identityAria")}>
      <span className="fleet-selection__eyebrow">{t("fleet.detail.identity")}</span>
      <strong className="fleet-selection__title">{node.id}</strong>
      <span className="fleet-selection__fact">
        {t("fleet.detail.traffic", { inbound: node.inbound, outbound: node.outbound })}
      </span>
      <span className="fleet-selection__fact">
        {t("fleet.detail.deliverySummary", {
          delivered: node.delivered,
          deferred: node.deferred,
          failed: node.failed,
        })}
      </span>
      <span className="fleet-selection__fact">
        {t("fleet.detail.lastActivity", { relative: timeAgo(node.lastTs, t) })}
      </span>
      {canMessage && node.exact && onMessagePeer !== undefined && (
        <button type="button" className="fleet-selection__action" onClick={() => onMessagePeer(node.id)}>
          {t("fleet.detail.messagePeer")}
        </button>
      )}
      <small>{t("fleet.detail.chatNote")}</small>
    </aside>
  );
}

export function FleetProjectDetail({ project }: { readonly project: ProjectTraffic }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <aside className="fleet-selection" aria-label={t("fleet.detail.projectAria")}>
      <span className="fleet-selection__eyebrow">{t("fleet.detail.project")}</span>
      <strong className="fleet-selection__title">{project.id}</strong>
      <span className="fleet-selection__fact">
        {countLabel(project.members.length, "fleet.noun.identity", "fleet.noun.identities", t)}
      </span>
      <span className="fleet-selection__fact">
        {t("fleet.detail.traffic", { inbound: project.inbound, outbound: project.outbound })}
      </span>
      <span className="fleet-selection__fact">
        {countLabel(project.claims, "fleet.noun.claim", "fleet.noun.claims", t)}
      </span>
    </aside>
  );
}

function responseResult(result: OperatorActionResult, t: Translator): string {
  switch (result.kind) {
    case "accepted":
      return result.detail || t("fleet.detail.responseAccepted", { status: result.status });
    case "denied":
    case "rejected":
    case "unreachable":
    case "rate-limited":
      return result.detail;
    case "not-armed":
      return t("fleet.detail.notArmed");
    case "unauthorised":
      return t("fleet.detail.bearerRefused");
    case "error":
      return result.message;
  }
}

function messageTime(ts: number, locale: CockpitLocale): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ts * 1000));
}

export function FleetEdgeDetail({
  edge,
  messages,
  canRespond,
  respond,
  outsideFilter,
  onClearFilter,
  onOpenEvent,
}: {
  readonly edge: CommunicationEdge;
  readonly messages: readonly ConversationMessage[];
  readonly canRespond: boolean;
  readonly respond: (input: MessageResponseInput) => Promise<OperatorActionResult>;
  readonly outsideFilter: boolean;
  readonly onClearFilter: () => void;
  readonly onOpenEvent?: ((seq: number) => void) | undefined;
}): JSX.Element {
  const { locale, t } = useCockpitI18n();
  const [messageSeq, setMessageSeq] = useState(messages[0]?.seq ?? 0);
  const [status, setStatus] = useState<SemanticResponseStatus>("acknowledged");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("");
  const [working, setWorking] = useState(false);
  const selected = messages.find((message) => message.seq === messageSeq) ?? messages[0];
  const evidence = useMemo(
    () => conversationEvidenceFor(messages, selected?.seq ?? 0),
    [messages, selected?.seq],
  );

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (selected === undefined || working) return;
    setWorking(true);
    setOutcome("");
    const result = await respond({
      messageSeq: selected.seq,
      to: selected.source,
      status,
      note,
    });
    setOutcome(responseResult(result, t));
    setWorking(false);
  };

  return (
    <aside className="fleet-conversation" aria-label={t("fleet.detail.linkAria")}>
      <header className="fleet-conversation__header">
        <span className="fleet-selection__eyebrow">{t("fleet.detail.link")}</span>
        <strong>
          {edge.source} → {edge.target}
        </strong>
        <span>
          {t("fleet.detail.sentSummary", {
            sent: edge.messages,
            delivered: edge.delivered,
            deferred: edge.deferred,
            failed: edge.failed,
          })}
        </span>
      </header>
      {outsideFilter && (
        <div className="fleet-conversation__filter-note" role="status">
          <span>{t("fleet.detail.pinned")}</span>
          <button type="button" onClick={onClearFilter}>{t("fleet.detail.showRoute")}</button>
        </div>
      )}
      <div className="fleet-conversation__timeline" aria-label={t("fleet.detail.timelineAria")}>
        {messages.map((message) => (
          <button
            key={message.seq}
            type="button"
            aria-pressed={selected?.seq === message.seq}
            className={`fleet-message${selected?.seq === message.seq ? " fleet-message--selected" : ""}`}
            onClick={() => setMessageSeq(message.seq)}
          >
            <span className="fleet-message__meta">
              <b>#{message.seq}</b> · {messageTime(message.ts, locale)} · {message.source} → {message.target}
            </span>
            <span className="fleet-message__body">{message.body || t("fleet.detail.emptyBody")}</span>
            <span className="fleet-message__signals">
              {t("fleet.detail.delivery", { status: message.delivery })}
              {message.responseStatus !== null && (
                <>
                  {" "}
                  · {message.responseEvidenceScope === "operator_commentary"
                    ? t("fleet.detail.operatorCommentary")
                    : message.responseEvidenceScope === "recipient"
                      ? t("fleet.detail.recipientResponse")
                      : t("fleet.detail.legacyResponse")}{" "}
                  {message.responseToSeq === null
                    ? message.responseStatus
                    : t("fleet.detail.responseSignal", {
                        status: message.responseStatus,
                        seq: message.responseToSeq,
                      })}
                </>
              )}
            </span>
          </button>
        ))}
      </div>
      {evidence !== null && (
        <section
          className="fleet-evidence"
          aria-label={t("fleet.detail.evidenceAria", { seq: evidence.message.seq })}
        >
          <header>
            <div>
              <span className="fleet-selection__eyebrow">{t("fleet.detail.evidenceChain")}</span>
              <strong>{t("fleet.detail.message", { seq: evidence.message.seq })}</strong>
            </div>
            {onOpenEvent !== undefined && (
              <button type="button" onClick={() => onOpenEvent(evidence.message.seq)}>
                {t("fleet.detail.openExact")}
              </button>
            )}
          </header>
          <ol>
            <li>
              <span>{t("fleet.detail.durableChat")}</span>
              <strong>{`${evidence.message.source} → ${evidence.message.target}`}</strong>
              <small>{t("fleet.detail.sequence", { seq: evidence.message.seq })}</small>
            </li>
            <li>
              <span>{t("fleet.detail.transportReceipt")}</span>
              <strong>{evidence.message.delivery}</strong>
              <small>
                {evidence.message.delivery === "unknown"
                  ? t("fleet.detail.noTransport")
                  : t("fleet.detail.correlated")}
              </small>
            </li>
            <li>
              <span>{t("fleet.detail.semanticResponse")}</span>
              {evidence.responses.length === 0 ? (
                <>
                  <strong>{t("fleet.detail.noneRetained")}</strong>
                  <small>{t("fleet.detail.absence")}</small>
                </>
              ) : (
                <ul>
                  {evidence.responses.map((response) => (
                    <li key={response.seq}>
                      <strong>{`${response.responseStatus ?? t("fleet.detail.unclassified")} · ${response.responseEvidenceScope ?? t("fleet.detail.legacyScope")}`}</strong>
                      <small>{t("fleet.detail.responseLink", { response: response.seq, message: evidence.message.seq })}</small>
                      {onOpenEvent !== undefined && (
                        <button type="button" onClick={() => onOpenEvent(response.seq)}>
                          {t("fleet.detail.openResponse")}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          </ol>
        </section>
      )}
      {canRespond && selected !== undefined ? (
        <form className="fleet-response" onSubmit={(event) => void submit(event)}>
          <label>
            {t("fleet.detail.respondTo", { seq: selected.seq })}
            <select value={status} onChange={(event) => setStatus(event.target.value as SemanticResponseStatus)}>
              <option value="acknowledged">acknowledged</option>
              <option value="in_progress">in progress</option>
              <option value="needs_input">needs input</option>
              <option value="declined">declined</option>
              <option value="completed">completed</option>
            </select>
          </label>
          <label className="fleet-response__note">
            {t("fleet.detail.optionalNote")}
            <input value={note} maxLength={2048} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button type="submit" disabled={working}>
            {working ? t("fleet.detail.sending") : t("fleet.detail.sendResponse")}
          </button>
          {outcome !== "" && <output aria-live="polite">{outcome}</output>}
          <small>{t("fleet.detail.commentaryNote")}</small>
        </form>
      ) : (
        <small className="fleet-conversation__viewer-note">{t("fleet.detail.readOnly")}</small>
      )}
    </aside>
  );
}
