/**
 * Keyboard shortcuts (newplan §15.1). Single-key shortcuts never fire while
 * the user is typing; modifier shortcuts (Ctrl/Cmd+K) work everywhere.
 */
export interface ShortcutDef {
  keys: string[];
  description: string;
  group: "Global" | "Overlays";
}

export const SHORTCUTS: readonly ShortcutDef[] = [
  { keys: ["Ctrl / ⌘", "K"], description: "Open the command palette", group: "Global" },
  { keys: ["?"], description: "Show keyboard shortcuts", group: "Global" },
  { keys: ["↑", "↓"], description: "Move through palette results", group: "Overlays" },
  { keys: ["Enter"], description: "Open the selected result", group: "Overlays" },
  { keys: ["Esc"], description: "Close the open drawer, dialog or menu", group: "Overlays" },
];

/** True when the event target is a text field, select or contenteditable region. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (target as HTMLInputElement).type;
  return !["button", "checkbox", "radio", "range", "reset", "submit", "color", "file", "image"].includes(type);
}

/** Ctrl+K on Windows / Linux, Cmd+K on macOS. */
export function isPaletteShortcut(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "k";
}

/** `?` with no Ctrl / Cmd / Alt, outside text fields. */
export function isHelpShortcut(event: KeyboardEvent): boolean {
  return event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey && !isTypingTarget(event.target);
}
