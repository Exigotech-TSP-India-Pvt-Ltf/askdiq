import { OUTCOMES, REASON_TAGS, TRIAGE_LABELS, TRIAGE_STATUSES, reasonLabel } from "@/lib/feedback-api";
import { DEFAULT_STATUS, outcomeLabel, type FeedbackFilters } from "@/lib/feedback-filters";
import type { FilterChoice, FilterDescriptor } from "@/components/admin/filters/types";

const ANY: FilterChoice = { value: "", label: "Any" };

/** Status choices come from `TRIAGE_STATUSES` (plus All), never a hard-coded list. */
export const STATUS_CHOICES: readonly FilterChoice[] = [
  ...TRIAGE_STATUSES.map((s) => ({ value: s, label: TRIAGE_LABELS[s] })),
  { value: "all", label: "All" },
];
const RATING_CHOICES: readonly FilterChoice[] = [ANY, ...[1, 2, 3, 4, 5].map((r) => ({ value: String(r), label: String(r) }))];
const SENTIMENT_CHOICES: readonly FilterChoice[] = [
  ANY,
  { value: "positive", label: "Positive (4-5)" },
  { value: "negative", label: "Negative (1-2)" },
];
const OUTCOME_CHOICES: readonly FilterChoice[] = [ANY, ...OUTCOMES.map((o) => ({ value: o, label: outcomeLabel(o) }))];
const GROUNDED_CHOICES: readonly FilterChoice[] = [
  ANY,
  { value: "true", label: "Grounded" },
  { value: "false", label: "Ungrounded" },
];
const REASON_CHOICES: readonly FilterChoice[] = [ANY, ...REASON_TAGS.map((r) => ({ value: r, label: reasonLabel(r) }))];
const COMMENT_CHOICES: readonly FilterChoice[] = [
  ANY,
  { value: "true", label: "Has comment" },
  { value: "false", label: "No comment" },
];

function bool(value: boolean | undefined): string {
  return value === undefined ? "" : String(value);
}

/**
 * The Review Queue filters as `FilterChipBar` descriptors, in the order the
 * chips always had. Values round-trip through `applyFilters` / `parseFilters`,
 * so the URL keys and their validation are unchanged.
 */
export function feedbackFilterDescriptors(f: FeedbackFilters): FilterDescriptor[] {
  const sentiment = f.sentiment ?? "";
  return [
    {
      key: "status",
      label: "Status",
      value: f.status,
      kind: "single",
      options: STATUS_CHOICES,
      defaultValue: DEFAULT_STATUS,
      clearedValue: "all",
      showAtDefault: true,
    },
    { key: "rating", label: "Rating", value: f.rating === undefined ? "" : String(f.rating), kind: "single", options: RATING_CHOICES },
    {
      key: "sentiment",
      label: "Sentiment",
      value: sentiment,
      kind: "single",
      options: SENTIMENT_CHOICES,
      chipLabel: SENTIMENT_CHOICES.find((c) => c.value === sentiment && c.value)?.label,
    },
    { key: "outcome", label: "Outcome", value: f.outcome ?? "", kind: "single", options: OUTCOME_CHOICES },
    {
      key: "grounded",
      label: "Grounded",
      value: bool(f.grounded),
      kind: "single",
      options: GROUNDED_CHOICES,
      chipLabel: f.grounded === undefined ? undefined : f.grounded ? "Grounded" : "Ungrounded",
    },
    { key: "day", label: "Day", value: f.day ?? "", kind: "text" },
    { key: "reason", label: "Reason", value: f.reason ?? "", kind: "single", options: REASON_CHOICES },
    {
      key: "has_comment",
      label: "Comment",
      value: bool(f.has_comment),
      kind: "single",
      options: COMMENT_CHOICES,
      chipLabel: f.has_comment === undefined ? undefined : f.has_comment ? "Has comment" : "No comment",
    },
    {
      key: "message_id",
      label: "Message",
      value: f.message_id ?? "",
      kind: "text",
      chipLabel: f.message_id ? `Message: ${f.message_id.slice(0, 8)}` : undefined,
    },
    { key: "q", label: "Search", value: f.q ?? "", kind: "text" },
  ];
}
