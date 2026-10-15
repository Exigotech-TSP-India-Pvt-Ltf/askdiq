"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Compass, X } from "lucide-react";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import type { AnalyticsTabId } from "./url-state";

export interface TourStep {
  title: string;
  body: string;
  /** Tab the step points at ("Show me"). */
  tab?: AnalyticsTabId;
}

/** The HTML's `STEPS`, rewritten for our definitions. */
export const TOUR_STEPS: readonly TourStep[] = [
  {
    title: "Welcome to Analytics",
    body: "This page shows how people use the assistant: what they ask, whether it was answered, and where conversations drop. Use Next to continue or dismiss the tour at any time.",
  },
  {
    title: "Key figures",
    body: "The cards show conversations, substantive questions, the handled and served rates, probable people and average questions per conversation for the current filters.",
    tab: "overview",
  },
  {
    title: "Filters",
    body: "Set a period, user type, outcome, rating or search text. Active filters show as removable chips and are kept in the address bar, so a filtered view can be shared.",
    tab: "overview",
  },
  {
    title: "Charts are clickable",
    body: "Hover or focus any chart for exact numbers. Click a day, a flow band, a topic tile, a donut slice, a drop-off reason or a person to filter the whole page; click it again to clear.",
    tab: "trend",
  },
  {
    title: "Flow and topics",
    body: "The flow shows user types leading to outcomes. The topic map sizes each question type by volume and shades it from red (rarely handled) to teal (always handled).",
    tab: "topics",
  },
  {
    title: "Conversation drill-down",
    body: "Sort any column, page through results, use View to read every question and how it was handled, and open the id for the full conversation log.",
    tab: "conversations",
  },
];

/** Metric definitions (newplan §6.4), in plain words. */
export const METRIC_DEFINITIONS: readonly { term: string; definition: string }[] = [
  { term: "Conversation", definition: "One chat session. A session ends after 60 minutes without activity." },
  { term: "Exchange", definition: "One user message and the assistant's reply to it." },
  {
    term: "Substantive question",
    definition: "An exchange whose intent is not Greeting or Closing. Only these count towards topics, quality and the handled rate.",
  },
  {
    term: "Handled",
    definition: "The answer was correct and grounded in the documents (Answered correctly), or a correct refusal (Correct refusal).",
  },
  { term: "Questions handled", definition: "Handled substantive questions divided by all substantive questions." },
  {
    term: "Outcome",
    definition: "Served: every substantive question handled. Partly served: some. Not served: none. Greeting only: no substantive question.",
  },
  { term: "Conversations served", definition: "Served conversations divided by conversations that are not Greeting only." },
  {
    term: "User type",
    definition: "The intent group holding at least 60 % of a conversation's questions, otherwise Mixed.",
  },
  {
    term: "Rating",
    definition: "From user feedback first (1-2 stars: Dropped, 5 stars on a served conversation: Great), otherwise derived from the outcome.",
  },
  {
    term: "Issue",
    definition: "Why the last unhandled question failed, for example Info not in knowledge base. Drop-off counts issues over Partly and Not served conversations.",
  },
  {
    term: "Probable person",
    definition: "A stable pseudonym from the signed-in email (or network and browser for older chats). Shown as P01, P02 per result; no personal data is displayed.",
  },
  {
    term: "Hours and days",
    definition: "Bucketed in your browser's time zone.",
  },
  {
    term: "Pending classification",
    definition: "Turns not yet labelled by the classifier. They are left out of topics and quality until labelled.",
  },
];

const STORAGE_KEY = "deployiq.analytics.tourDismissed";

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeDismissed(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, "1");
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable: the tour simply shows again next time
  }
}

const BUTTON =
  "rounded-lg border border-line bg-surface-card px-3 py-1.5 text-xs font-medium text-fg transition-colors duration-150 hover:border-brand-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-default disabled:opacity-40 dark:hover:border-brand-700/60";
const PRIMARY =
  "rounded-lg border border-brand-600 bg-brand-600 px-3 py-1.5 text-xs font-semibold text-fg-inverse transition-colors duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-card dark:border-brand-400 dark:bg-brand-400 dark:hover:bg-brand-300";

/** Dismissible step-through tour (the HTML's `gShow()`), non-modal. */
export function Tour({ onShow }: { onShow: (tab: AnalyticsTabId) => void }) {
  const [dismissed, setDismissed] = useState(false);
  const [step, setStep] = useState(0);
  const variants = useMotionVariants(fadeUp);

  useEffect(() => setDismissed(readDismissed()), []);

  const dismiss = (value: boolean) => {
    setDismissed(value);
    writeDismissed(value);
    if (!value) setStep(0);
  };

  if (dismissed) {
    return (
      <button type="button" className={`${BUTTON} inline-flex items-center gap-1.5`} onClick={() => dismiss(false)}>
        <Compass className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        Restart tour
      </button>
    );
  }

  const s = TOUR_STEPS[step];
  const last = step === TOUR_STEPS.length - 1;
  return (
    <section
      aria-labelledby="analytics-tour-title"
      aria-roledescription="tour"
      onKeyDown={(e) => {
        if (e.key === "Escape") dismiss(true);
      }}
      className="relative rounded-2xl border border-brand-500/60 bg-surface-card p-5 shadow-md dark:border-brand-400/40"
    >
      <button
        type="button"
        aria-label="Dismiss tour"
        onClick={() => dismiss(true)}
        className="absolute right-3 top-3 rounded-md p-1 text-fg-subtle hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </button>
      <p className="text-[11px] font-bold uppercase tracking-wide text-brand-700 dark:text-brand-300" aria-live="polite">
        Step {step + 1} of {TOUR_STEPS.length}
      </p>
      <AnimatePresence mode="wait" initial={false}>
        <m.div key={step} variants={variants} initial="hidden" animate="visible" exit="exit">
          <h3 id="analytics-tour-title" className="mt-1 text-base font-semibold text-fg">
            {s.title}
          </h3>
          <p className="mt-1 max-w-prose text-sm text-fg-muted">{s.body}</p>
        </m.div>
      </AnimatePresence>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <button type="button" className={BUTTON} onClick={() => dismiss(true)}>
          Skip
        </button>
        <div className="flex gap-2">
          {s.tab && (
            <button type="button" className={BUTTON} onClick={() => s.tab && onShow(s.tab)}>
              Show me
            </button>
          )}
          <button type="button" className={BUTTON} disabled={step === 0} onClick={() => setStep((v) => Math.max(0, v - 1))}>
            Back
          </button>
          <button type="button" className={PRIMARY} onClick={() => (last ? dismiss(true) : setStep((v) => v + 1))}>
            {last ? "Finish" : "Next"}
          </button>
        </div>
      </div>
    </section>
  );
}

/** Guide tab: the tour plus static metric definitions. */
export function GuideTab({ onShow }: { onShow: (tab: AnalyticsTabId) => void }) {
  return (
    <div className="flex flex-col gap-4">
      <Tour onShow={onShow} />
      <section aria-labelledby="analytics-definitions" className="rounded-2xl border border-line bg-surface-card p-5">
        <h2 id="analytics-definitions" className="text-sm font-semibold text-fg">
          How each number is calculated
        </h2>
        <dl className="mt-3 grid gap-x-6 gap-y-3 md:grid-cols-2">
          {METRIC_DEFINITIONS.map((d) => (
            <div key={d.term}>
              <dt className="text-xs font-semibold text-fg">{d.term}</dt>
              <dd className="mt-0.5 text-xs text-fg-muted">{d.definition}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
