import { focusRing } from "@/components/ui/styles";

/**
 * Where an account-corner popover opens: `above` the expanded sidebar's action
 * row, or to the `side` of the collapsed rail. The trigger wrapper stays
 * static, so the panel anchors to the action row / rail (`relative`) and is
 * kept on screen from the bottom-left corner.
 */
export type AccountPopoverPlacement = "above" | "side";

export const accountPopoverPosition: Record<AccountPopoverPlacement, string> = {
  above: "bottom-full left-0 mb-2 origin-bottom-left",
  side: "bottom-0 left-full ml-2 origin-bottom-left",
};

/** Equal-sized bordered icon button of the sidebar account corner (matches ThemeToggle `sm`). */
export const accountActionButton = `relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink/10 bg-surface-card text-fg-muted shadow-xs transition-colors hover:border-brand-500/40 hover:bg-brand-50/50 hover:text-fg dark:border-white/10 dark:bg-dark-surface dark:hover:bg-brand-950/40 dark:hover:text-white ${focusRing}`;
