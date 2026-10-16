/** Guided product tours: one tour per dashboard page (and optionally per sub-tab). */

export type TourPlacement = "top" | "bottom" | "left" | "right" | "center";

export interface TourStep {
  /** CSS selector of the element to spotlight, normally `[data-tour="<page>-<thing>"]`.
   *  Omit (or use placement "center") for an intro/outro card with no spotlight. */
  target?: string;
  title: string;
  /** One to three short sentences: what it is, what it means, what you can do. */
  body: string;
  placement?: TourPlacement;
  /** Optional: switch to this sub-tab (the page's `?tab=` value) before showing the step. */
  tab?: string;
}

export interface PageTour {
  /** Stable id, also the localStorage "seen" key. */
  id: TourId;
  /** Shown in the tour launcher, e.g. "Governance". */
  title: string;
  steps: TourStep[];
}

export type TourId =
  | "shell"
  | "dashboard"
  | "chat"
  | "documents"
  | "visitors"
  | "usage"
  | "conversations"
  | "feedback"
  | "evaluation"
  | "governance"
  | "analytics"
  | "user-management"
  | "settings";
