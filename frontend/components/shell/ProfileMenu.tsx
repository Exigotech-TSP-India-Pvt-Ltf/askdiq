"use client";

import React, { useEffect, useId } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, LogOut, Monitor, Moon, Sun } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useTheme, type ThemeMode } from "@/lib/theme-context";
import { pop, useMotionVariants } from "@/lib/motion";
import { usePopover } from "@/lib/use-popover";
import { focusRing, menuItem, popoverPanel } from "@/components/ui/styles";

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: React.ElementType }[] = [
  { mode: "light", label: "Light", icon: Sun },
  { mode: "dark", label: "Dark", icon: Moon },
  { mode: "system", label: "System", icon: Monitor },
];

function initials(name: string | undefined | null): string {
  const letters = (name ?? "")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return letters || "?";
}

interface ProfileMenuProps {
  /** Role badge (PR-18 passes the signed-in user's role). */
  roleBadge?: React.ReactNode;
  /** Sign-out action; defaults to the auth context's `logout`. */
  onSignOut?: () => void;
}

/**
 * Avatar menu (newplan §15.1): name, email, role badge slot, theme
 * (light / dark / system) and sign out. Arrow keys move between items.
 */
export function ProfileMenu({ roleBadge, onSignOut }: ProfileMenuProps) {
  const { user, logout } = useAuth();
  const { mode, setTheme } = useTheme();
  const { open, toggle, close, triggerRef, panelRef } = usePopover();
  const variants = useMotionVariants(pop);
  const menuId = useId();
  const name = user?.full_name || "Signed out";

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("[role^='menuitem']")?.focus();
  }, [open, panelRef]);

  function onMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>("[role^='menuitem']") ?? []);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    const focusAt = (i: number) => items[(i + items.length) % items.length]?.focus();
    if (event.key === "ArrowDown") focusAt(index + 1);
    else if (event.key === "ArrowUp") focusAt(index - 1);
    else if (event.key === "Home") focusAt(0);
    else if (event.key === "End") focusAt(items.length - 1);
    else if (event.key === "Tab") close(false);
    else return;
    if (event.key !== "Tab") event.preventDefault();
  }

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Account menu for ${name}`}
        className={`flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white shadow-sm transition-transform hover:scale-[1.02] ${focusRing}`}
      >
        {initials(user?.full_name)}
      </button>
      <AnimatePresence>
        {open ? (
          <m.div
            ref={panelRef}
            id={menuId}
            role="menu"
            aria-label="Account"
            onKeyDown={onMenuKeyDown}
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`absolute right-0 z-50 mt-2 w-64 origin-top-right ${popoverPanel}`}
          >
            <div className="flex items-center gap-3 px-2.5 pb-2.5 pt-1.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                {initials(user?.full_name)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-fg">{name}</p>
                {user?.email ? <p className="truncate text-xs text-fg-subtle">{user.email}</p> : null}
                {roleBadge ? <div className="mt-1">{roleBadge}</div> : null}
              </div>
            </div>
            <div role="group" aria-label="Theme" className="border-t border-line pt-1.5">
              <p className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Theme</p>
              {THEME_OPTIONS.map(({ mode: option, label, icon: Icon }) => (
                <button
                  key={option}
                  type="button"
                  role="menuitemradio"
                  aria-checked={mode === option}
                  tabIndex={-1}
                  onClick={() => setTheme(option)}
                  className={menuItem}
                >
                  <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  <span className="flex-1">{label}</span>
                  {mode === option ? (
                    <Check className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
                  ) : null}
                </button>
              ))}
            </div>
            <div className="mt-1.5 border-t border-line pt-1.5">
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                onClick={() => {
                  close();
                  (onSignOut ?? logout)();
                }}
                className={menuItem}
              >
                <LogOut className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                Sign out
              </button>
            </div>
          </m.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
