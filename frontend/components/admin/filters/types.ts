/** Shared filter-chip model (OR-13): Feedback, Analytics and every TableToolbar table. */

export interface FilterChoice {
  value: string;
  label: string;
}

export interface FilterDescriptor {
  /** URL / state key, e.g. "status". */
  key: string;
  /** Field name shown on the chip and in the Filters popover, e.g. "Status". */
  label: string;
  /** Current value ("" when unset). */
  value: string;
  /**
   * Enumerated values (`single`): the chip opens them as a listbox and the
   * Filters popover lists them. `text` filters (search, a picked person) have
   * none: the chip only shows the value and can be removed.
   */
  options?: readonly FilterChoice[];
  kind: "single" | "text";
  /** Default value: not counted as active (default ""). */
  defaultValue?: string;
  /** A value that means "no filter" besides the default (e.g. status "all"). */
  clearedValue?: string;
  /** Value written by the chip's × (default: `clearedValue`, else `defaultValue`, else ""). */
  resetTo?: string;
  /** Chip text when it should differ from "Label: Value" (e.g. "Negative (1-2)"). */
  chipLabel?: string;
  /** Keep the chip visible at the default value (Feedback "Status: New"). */
  showAtDefault?: boolean;
  /** List the field in the Filters popover (default: when it has options). */
  inPopover?: boolean;
}

export function isCleared(d: FilterDescriptor): boolean {
  return d.value === "" || d.value === (d.clearedValue ?? "");
}

/** Active = neither the default nor a cleared value. */
export function isActive(d: FilterDescriptor): boolean {
  return !isCleared(d) && d.value !== (d.defaultValue ?? "");
}

/** Whether the field shows a chip. */
export function hasChip(d: FilterDescriptor): boolean {
  if (isCleared(d)) return false;
  return isActive(d) || Boolean(d.showAtDefault);
}

export function valueLabel(d: FilterDescriptor): string {
  return d.options?.find((o) => o.value === d.value)?.label ?? d.value;
}

export function chipText(d: FilterDescriptor): string {
  return d.chipLabel ?? `${d.label}: ${valueLabel(d)}`;
}

export function resetValue(d: FilterDescriptor): string {
  return d.resetTo ?? d.clearedValue ?? d.defaultValue ?? "";
}

export function activeCount(filters: readonly FilterDescriptor[]): number {
  return filters.filter(isActive).length;
}
