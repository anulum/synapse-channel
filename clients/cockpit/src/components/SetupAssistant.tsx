// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — read-only local setup assistant

import type { JSX, MouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import {
  buildSetupCommandPlan,
  deriveSetupPreflight,
  deriveSetupProof,
  isSafeSetupCommand,
  type SetupCommand,
  type SetupEvidence,
  type SetupProfileInput,
} from "../lib/setupAssistant";

const STEPS = ["preflight", "profile", "commands", "verify"] as const;
type SetupStep = (typeof STEPS)[number];

interface SetupAssistantProps {
  readonly open: boolean;
  readonly evidence: SetupEvidence;
  readonly onClose: () => void;
}

const DEFAULT_PROFILE: SetupProfileInput = Object.freeze({
  hubPort: "8876",
  dashboardPort: "8765",
  durableEvidence: false,
  protectedDashboard: false,
});

export function SetupAssistant({ open, evidence, onClose }: SetupAssistantProps): JSX.Element | null {
  const { t } = useCockpitI18n();
  const [step, setStep] = useState<SetupStep>("preflight");
  const [profile, setProfile] = useState<SetupProfileInput>(DEFAULT_PROFILE);
  const [copyState, setCopyState] = useState<{ readonly id: SetupCommand["id"]; readonly ok: boolean } | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const preflight = useMemo(() => deriveSetupPreflight(evidence), [evidence]);
  const proof = useMemo(() => deriveSetupProof(evidence), [evidence]);
  const plan = useMemo(() => buildSetupCommandPlan(profile), [profile]);
  const stepIndex = STEPS.indexOf(step);

  useEffect(() => {
    if (!open) return;
    setStep("preflight");
    setProfile(DEFAULT_PROFILE);
    setCopyState(null);
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled])",
        ) ?? [],
      );
      const first = focusable[0];
      const last = focusable.at(-1);
      if (first === undefined || last === undefined) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  const closeFromVeil = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) onClose();
  };

  const updateProfile = <Key extends keyof SetupProfileInput>(
    key: Key,
    value: SetupProfileInput[Key],
  ): void => {
    setProfile((current) => ({ ...current, [key]: value }));
    setCopyState(null);
  };

  const copyCommand = async (command: SetupCommand): Promise<void> => {
    if (!isSafeSetupCommand(command.text) || navigator.clipboard === undefined) {
      setCopyState({ id: command.id, ok: false });
      return;
    }
    try {
      await navigator.clipboard.writeText(command.text);
      setCopyState({ id: command.id, ok: true });
    } catch {
      setCopyState({ id: command.id, ok: false });
    }
  };

  const advance = (): void => {
    const next = STEPS[stepIndex + 1];
    if (next !== undefined) setStep(next);
  };
  const retreat = (): void => {
    const previous = STEPS[stepIndex - 1];
    if (previous !== undefined) setStep(previous);
  };

  return (
    <div className="setup-veil" onMouseDown={closeFromVeil}>
      <div
        ref={dialogRef}
        className="setup-assistant"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-title"
        aria-describedby="setup-privacy"
      >
        <div className="setup-assistant__head">
          <div>
            <span className="setup-assistant__eyebrow">{t("setup.eyebrow")}</span>
            <h2 id="setup-title">{t("setup.title")}</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="setup-assistant__close"
            onClick={onClose}
            aria-label={t("setup.close")}
          >
            ×
          </button>
        </div>

        <p id="setup-privacy" className="setup-assistant__privacy">{t("setup.localOnly")}</p>
        <p className="visually-hidden" aria-live="polite">
          {t("setup.progress", { current: stepIndex + 1, total: STEPS.length })}
        </p>

        <nav className="setup-steps" aria-label={t("setup.progress", { current: stepIndex + 1, total: STEPS.length })}>
          {STEPS.map((candidate, index) => (
            <button
              key={candidate}
              type="button"
              className={candidate === step ? "setup-step setup-step--active" : "setup-step"}
              aria-current={candidate === step ? "step" : undefined}
              disabled={!plan.ok && index > 1}
              onClick={() => setStep(candidate)}
            >
              <span aria-hidden="true">{index + 1}</span>
              {t(`setup.step.${candidate}`)}
            </button>
          ))}
        </nav>

        <div className="setup-assistant__body" tabIndex={0}>
          {step === "preflight" && (
            <section aria-labelledby="setup-preflight-title">
              <h3 id="setup-preflight-title">{t("setup.preflight.title")}</h3>
              <p>{t("setup.preflight.body")}</p>
              <div className="setup-evidence-list">
                {preflight.map((row) => (
                  <article key={row.id} className="setup-evidence-row">
                    <div>
                      <strong>{t(`setup.preflight.${row.id}.title`)}</strong>
                      <p>{t(`setup.preflight.${row.id}.body`)}</p>
                    </div>
                    <span className={`setup-status setup-status--${row.truth}`}>
                      {t(`setup.truth.${row.truth}`)}
                    </span>
                  </article>
                ))}
              </div>
            </section>
          )}

          {step === "profile" && (
            <section aria-labelledby="setup-profile-title">
              <h3 id="setup-profile-title">{t("setup.profile.title")}</h3>
              <p>{t("setup.profile.body")}</p>
              <div className="setup-profile-grid">
                <label>
                  <span>{t("setup.profile.host")}</span>
                  <output>{t("setup.profile.hostValue")}</output>
                </label>
                <label>
                  <span>{t("setup.profile.hubPort")}</span>
                  <input
                    inputMode="numeric"
                    value={profile.hubPort}
                    onChange={(event) => updateProfile("hubPort", event.target.value)}
                    aria-invalid={!plan.ok && plan.error === "hub-port"}
                  />
                </label>
                <label>
                  <span>{t("setup.profile.dashboardPort")}</span>
                  <input
                    inputMode="numeric"
                    value={profile.dashboardPort}
                    onChange={(event) => updateProfile("dashboardPort", event.target.value)}
                    aria-invalid={!plan.ok && (plan.error === "dashboard-port" || plan.error === "port-collision")}
                  />
                </label>
              </div>
              {!plan.ok && <p className="setup-error" role="alert">{t(`setup.error.${plan.error}`)}</p>}
              <div className="setup-options">
                <label>
                  <input
                    type="checkbox"
                    aria-label={t("setup.profile.durable")}
                    checked={profile.durableEvidence}
                    onChange={(event) => updateProfile("durableEvidence", event.target.checked)}
                  />
                  <span><strong>{t("setup.profile.durable")}</strong>{t("setup.profile.durableHelp")}</span>
                </label>
                <label>
                  <input
                    type="checkbox"
                    aria-label={t("setup.profile.protected")}
                    checked={profile.protectedDashboard}
                    onChange={(event) => updateProfile("protectedDashboard", event.target.checked)}
                  />
                  <span><strong>{t("setup.profile.protected")}</strong>{t("setup.profile.protectedHelp")}</span>
                </label>
              </div>
              <p className="setup-boundary">{t("setup.profile.boundary")}</p>
            </section>
          )}

          {step === "commands" && plan.ok && (
            <section aria-labelledby="setup-commands-title">
              <h3 id="setup-commands-title">{t("setup.commands.title")}</h3>
              <p>{t("setup.commands.body")}</p>
              <div className="setup-command-list">
                {plan.commands.map((command) => (
                  <article key={command.id} className="setup-command">
                    <div className="setup-command__head">
                      <strong>{t(`setup.commands.${command.id}`)}</strong>
                      <button
                        type="button"
                        disabled={!isSafeSetupCommand(command.text)}
                        onClick={() => void copyCommand(command)}
                      >
                        {t("setup.commands.copy")}
                      </button>
                    </div>
                    <code>{command.text}</code>
                    {copyState?.id === command.id && (
                      <span className="setup-command__feedback" role="status">
                        {copyState.ok ? t("setup.commands.copied") : t("setup.commands.copyFailed")}
                      </span>
                    )}
                  </article>
                ))}
              </div>
              <p className="setup-boundary">{t("setup.commands.placeholders")}</p>
            </section>
          )}

          {step === "verify" && (
            <section aria-labelledby="setup-verify-title">
              <h3 id="setup-verify-title">{t("setup.verify.title")}</h3>
              <p>{t("setup.verify.body")}</p>
              <div className="setup-proof-list">
                {proof.map((row) => (
                  <article key={row.id} className="setup-proof-row">
                    <span className={`setup-proof-mark setup-proof-mark--${row.verdict}`} aria-hidden="true" />
                    <div>
                      <strong>{t(`setup.proof.${row.id}.title`)}</strong>
                      <p>{t(`setup.proof.${row.id}.body`)}</p>
                    </div>
                    <span className={`setup-status setup-status--${row.verdict}`}>
                      {t(`setup.verdict.${row.verdict}`)}
                    </span>
                  </article>
                ))}
              </div>
            </section>
          )}
        </div>

        <footer className="setup-assistant__foot">
          <button type="button" onClick={retreat} disabled={stepIndex === 0}>{t("setup.back")}</button>
          {stepIndex < STEPS.length - 1 ? (
            <button type="button" className="setup-assistant__next" onClick={advance} disabled={!plan.ok && stepIndex >= 1}>
              {t("setup.next")}
            </button>
          ) : (
            <button type="button" className="setup-assistant__next" onClick={onClose}>{t("setup.done")}</button>
          )}
        </footer>
      </div>
    </div>
  );
}
