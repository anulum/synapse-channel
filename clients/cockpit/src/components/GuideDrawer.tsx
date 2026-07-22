// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — searchable, contextual in-product cockpit guide

import type { JSX, MouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import { filterGuideTopics, guideTopics } from "../lib/guide";
import type { CockpitLocale } from "../lib/i18n";
import type { InspectorTab } from "../lib/workspace";

interface GuideDrawerProps {
  readonly open: boolean;
  readonly activePanel: InspectorTab;
  readonly onClose: () => void;
  readonly onOpenSetup?: (() => void) | undefined;
}

export function GuideDrawer({ open, activePanel, onClose, onOpenSetup }: GuideDrawerProps): JSX.Element | null {
  const { locale, setLocale, t } = useCockpitI18n();
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const topics = useMemo(() => guideTopics(t, activePanel), [activePanel, t]);
  const shownTopics = useMemo(
    () => filterGuideTopics(topics, query, locale),
    [locale, query, topics],
  );

  useEffect(() => {
    if (!open) return;
    setQuery("");
    searchRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        drawerRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), summary",
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

  return (
    <div className="guide-veil" onMouseDown={closeFromVeil}>
      <div
        ref={drawerRef}
        className="guide-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-title"
        aria-describedby="guide-privacy"
      >
        <div className="guide-drawer__head">
          <div>
            <span className="guide-drawer__eyebrow">{t("guide.context")}</span>
            <h2 id="guide-title">{t("guide.title")}</h2>
          </div>
          <button type="button" className="guide-drawer__close" onClick={onClose} aria-label={t("guide.close")}>
            ×
          </button>
        </div>

        <div className="guide-drawer__tools">
          <label className="guide-drawer__search">
            <span className="visually-hidden">{t("guide.searchLabel")}</span>
            <input
              ref={searchRef}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("guide.searchPlaceholder")}
              aria-label={t("guide.searchLabel")}
            />
          </label>
          <label className="guide-drawer__locale">
            <span>{t("hud.locale")}</span>
            <select
              value={locale}
              onChange={(event) => setLocale(event.target.value as CockpitLocale)}
              aria-label={t("hud.locale")}
            >
              <option value="en">English</option>
              <option value="sk">Slovenčina</option>
            </select>
          </label>
        </div>

        <p id="guide-privacy" className="guide-drawer__privacy">{t("guide.localOnly")}</p>
        <p className="guide-drawer__shortcut">{t("guide.shortcut")}</p>
        {onOpenSetup !== undefined && (
          <button type="button" className="guide-drawer__setup" onClick={onOpenSetup}>
            {t("guide.openSetup")}
          </button>
        )}

        <div className="guide-drawer__topics" aria-live="polite">
          {shownTopics.length === 0 ? (
            <p className="guide-drawer__empty">{t("guide.noResults")}</p>
          ) : (
            shownTopics.map((topic, index) => (
              <details key={topic.id} className="guide-topic" open={index === 0}>
                <summary>
                  <strong>{topic.title}</strong>
                  <span>{topic.summary}</span>
                </summary>
                <p>{topic.body}</p>
              </details>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
