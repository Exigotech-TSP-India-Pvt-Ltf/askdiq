import { fireEvent, screen, within } from "@testing-library/react";

/** Opens the shared "Filters" popover (OR-13) if needed and returns it. */
export async function openFiltersPopover(): Promise<HTMLElement> {
  const open = screen.queryByRole("dialog", { name: "Filters" });
  if (open) return open;
  // The page-level chip bar (tables have their own "Active table filters").
  const bar = await screen.findByRole("region", { name: "Active filters" });
  fireEvent.click(within(bar).getByRole("button", { name: /^Filters/ }));
  return screen.getByRole("dialog", { name: "Filters" });
}

/** Picks `option` for `field` in the Filters popover (replaces the old native selects). */
export async function pickFilter(field: string, option: string): Promise<void> {
  const popover = await openFiltersPopover();
  fireEvent.click(within(within(popover).getByRole("group", { name: field })).getByRole("button", { name: option }));
}
