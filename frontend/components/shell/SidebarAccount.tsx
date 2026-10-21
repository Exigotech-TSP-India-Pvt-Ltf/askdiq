"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import {
  Bell,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Compass,
  Gauge,
  Loader2,
  LogOut,
  Monitor,
  Moon,
  Palette,
  Sun,
  type LucideIcon,
} from "lucide-react";
import { RoleBadge } from "@/components/shell/RoleBadge";
import { MarkAllReadButton, NotificationList, useNotificationReadState } from "@/components/shell/NotificationBell";
import { accountPopoverPosition, type AccountPopoverPlacement } from "@/components/shell/account-actions";
import { useTourChoices, useTourHint, type TourChoice } from "@/components/tour/TourLauncher";
import { useTourOptional } from "@/components/tour/TourProvider";
import { focusRing, menuItem, popoverPanel } from "@/components/ui/styles";
import { useAuth } from "@/lib/auth-context";
import { initials } from "@/lib/format";
import { pop, useMotionVariants } from "@/lib/motion";
import { ROLE_LABELS, hasPermission } from "@/lib/permissions";
import { moveMenuFocus, menuItems, useMenu } from "@/lib/use-menu";
import { useShellNotifications, MONITORING_HREF } from "@/lib/use-shell-notifications";
import { useTheme, type ThemeMode } from "@/lib/theme-context";

const avatarClass = "flex shrink-0 items-center justify-center rounded-full bg-brand-600 font-semibold text-white shadow-sm ring-2 ring-surface-card";

type Submenu = "notifications" | "tours" | "theme";

const THEME_OPTIONS: ReadonlyArray<{ mode: ThemeMode; label: string; icon: LucideIcon }> = [
  { mode: "light", label: "Light", icon: Sun },
  { mode: "dark", label: "Dark", icon: Moon },
  { mode: "system", label: "System", icon: Monitor },
];
const THEME_LABEL: Record<ThemeMode, string> = { light: "Light", dark: "Dark", system: "System" };

interface SidebarAccountProps {
  collapsed: boolean;
  /** `drawer`: the mobile navigation drawer (its tour entry does not hide the floating launcher). */
  surface?: "sidebar" | "drawer";
  /** Runs before an action takes the user to the page (the mobile drawer closes). */
  onBeforeAction?: () => void;
}

function CountBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="ml-auto min-w-[1.25rem] rounded-full bg-status-danger px-1.5 text-center text-[10px] font-semibold leading-4 text-white dark:text-fg-inverse">
      {count > 9 ? "9+" : count}
    </span>
  );
}

const itemIcon = "h-4 w-4 shrink-0 text-fg-subtle";

/**
 * Sidebar footer (OR-3): one profile button (avatar, name, role badge and a
 * chevron) opening an APG menu: Notifications, Walkthrough guide and Theme
 * submenus, then Sign out. Submenus open with → / Enter and close with ← /
 * Esc (one level at a time); Esc on the top level returns focus to the
 * trigger, and an outside click or Tab closes. Collapsed, the trigger is the
 * avatar alone and the menu opens beside the rail.
 */
export function SidebarAccount({ collapsed, surface = "sidebar", onBeforeAction }: SidebarAccountProps) {
  const { user, logout, role, permissions } = useAuth();
  const notifications = useShellNotifications();
  const { unread, isUnread, markAllRead } = useNotificationReadState(notifications);
  const { mode, setTheme } = useTheme();
  const tour = useTourOptional();
  const { pageId, choices } = useTourChoices();
  const canMonitor = hasPermission(permissions, "dashboard:read");
  const menu = useMenu({ focusOnOpen: "none" });
  const { open, close, toggle, triggerRef, panelRef } = menu;
  const [level, setLevel] = useState<Submenu | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const subRef = useRef<HTMLDivElement>(null);
  const parentRefs = useRef<Partial<Record<Submenu, HTMLButtonElement | null>>>({});
  const returnTo = useRef<Submenu | null>(null);
  const typeaheadState = useRef({ text: "", at: 0 });
  const menuId = useId();
  const variants = useMotionVariants(pop);
  const registerHeaderLauncher = tour?.registerHeaderLauncher;
  const [hint, setHint] = useTourHint(tour ? pageId : null, anchorRef, tour?.headerLaunchers);

  // The sidebar menu replaces the floating tour launcher (the drawer one does not).
  useEffect(
    () => (surface === "sidebar" && registerHeaderLauncher ? registerHeaderLauncher() : undefined),
    [surface, registerHeaderLauncher],
  );

  // Every open starts at the top level; focus lands on the first item (or the
  // submenu's parent after ← / Esc) and on the first item of an opened submenu.
  useEffect(() => {
    if (!open) {
      setLevel(null);
      return;
    }
    if (level) {
      (menuItems(subRef.current)[0] ?? subRef.current)?.focus({ preventScroll: true });
    } else {
      const back = returnTo.current ? parentRefs.current[returnTo.current] : null;
      returnTo.current = null;
      (back ?? menuItems(mainRef.current)[0] ?? mainRef.current)?.focus({ preventScroll: true });
    }
  }, [open, level]);

  const name = user?.full_name || "Signed out";
  const email = user?.email ?? "";
  const roleLabel = role ? ROLE_LABELS[role] : null;
  const placement: AccountPopoverPlacement = collapsed ? "side" : "above";
  const showTours = Boolean(tour && pageId && choices.length > 0);
  const summary = [name, email, roleLabel].filter(Boolean).join("\n");
  const triggerLabel = `${name}, account menu${unread ? ` (${unread} new notification${unread === 1 ? "" : "s"})` : ""}`;

  function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    // logout() always ends on /login (bounded wait); reset only if it ever returns here.
    void Promise.resolve(logout()).finally(() => setSigningOut(false));
  }

  function openSub(next: Submenu) {
    setLevel(next);
  }

  function backToMain() {
    returnTo.current = level;
    setLevel(null);
  }

  function leaveMenu() {
    close(false);
    onBeforeAction?.();
  }

  function launchTour(choice: TourChoice) {
    setHint(false);
    leaveMenu();
    tour?.start(choice.id);
  }

  function onMainKeyDown(event: React.KeyboardEvent) {
    const target = event.target as HTMLElement;
    const sub = target.dataset.submenu as Submenu | undefined;
    if (sub && (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      openSub(sub);
      return;
    }
    moveWithin(mainRef.current, event);
  }

  /** Tab leaves (and closes) the menu; arrows, Home / End and typeahead move within `panel`. */
  function moveWithin(panel: HTMLElement | null, event: React.KeyboardEvent) {
    if (event.key === "Tab") {
      close(false);
      return;
    }
    if (moveMenuFocus(panel, event, typeaheadState.current)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function onSubKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowLeft" || event.key === "Escape") {
      // One level at a time: Esc here must not reach the popover's document listener.
      event.preventDefault();
      event.stopPropagation();
      backToMain();
      return;
    }
    moveWithin(subRef.current, event);
  }

  const subItem = `${menuItem} min-h-9`;

  function parentItem(id: Submenu, icon: LucideIcon, label: string, extra?: React.ReactNode, tourAnchor?: string) {
    const Icon = icon;
    return (
      <button
        ref={(el) => {
          parentRefs.current[id] = el;
        }}
        type="button"
        role="menuitem"
        tabIndex={-1}
        aria-haspopup="menu"
        aria-expanded={level === id}
        data-submenu={id}
        data-tour={tourAnchor}
        onClick={() => openSub(id)}
        className={`${subItem} justify-between`}
      >
        <Icon className={itemIcon} strokeWidth={1.75} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-medium text-fg">{label}</span>
        {extra}
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
      </button>
    );
  }

  function backItem(label: string) {
    return (
      <button type="button" role="menuitem" tabIndex={-1} onClick={backToMain} className={`${subItem} font-semibold text-fg`}>
        <ChevronLeft className="h-4 w-4 shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
        <span className="sr-only">Back to account menu: </span>
        {label}
      </button>
    );
  }

  const panelPosition = surface === "drawer" && !collapsed ? "bottom-full left-0 right-0 mb-2 origin-bottom" : accountPopoverPosition[placement];
  const panelWidth = collapsed ? "w-72" : "w-full min-w-[15rem]";

  return (
    <div data-tour="shell-account" className={`relative border-t border-line ${collapsed ? "flex justify-center p-3" : "px-2 py-3"}`}>
      <div ref={anchorRef} className={collapsed ? "relative" : "relative w-full"}>
        <button
          ref={triggerRef}
          type="button"
          onClick={() => {
            setHint(false);
            toggle();
          }}
          onKeyDown={menu.triggerProps.onKeyDown}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-label={triggerLabel}
          title={collapsed ? summary : undefined}
          data-testid="account-menu-trigger"
          className={
            collapsed
              ? `relative rounded-full ${focusRing}`
              : `flex w-full items-center gap-2 rounded-xl border border-line bg-surface-muted/50 p-2 text-left transition-colors hover:border-brand-300 hover:bg-surface-muted dark:bg-white/[0.03] dark:hover:border-brand-700/60 ${focusRing}`
          }
        >
          <span aria-hidden="true" className={`${avatarClass} relative h-9 w-9 text-xs`}>
            {user ? initials(user.full_name) : "—"}
            {unread > 0 ? (
              <span data-testid="account-unread-dot" className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-status-danger ring-2 ring-surface-card" />
            ) : null}
          </span>
          {hint ? (
            <span className="pointer-events-none absolute left-7 top-1 flex h-2.5 w-2.5" data-testid="tour-hint" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75 motion-reduce:animate-none" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-brand-500 ring-2 ring-surface-card" />
            </span>
          ) : null}
          {collapsed ? null : (
            <>
              <span className="min-w-0 flex-1">
                <span title={name} className="block truncate text-sm font-semibold leading-5 text-fg">
                  {name}
                </span>
                {role ? (
                  <span className="mt-0.5 block">
                    <RoleBadge role={role} />
                  </span>
                ) : null}
              </span>
              {open ? (
                <ChevronDown className="h-4 w-4 shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
              ) : (
                <ChevronUp className="h-4 w-4 shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
              )}
            </>
          )}
        </button>

        <AnimatePresence>
          {open ? (
            <m.div
              ref={panelRef}
              id={menuId}
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              data-placement={collapsed ? "side" : "above"}
              className={`absolute z-50 max-w-[calc(100vw-2rem)] ${panelWidth} ${panelPosition} ${popoverPanel}`}
            >
              {level === null ? (
                <div ref={mainRef} role="menu" aria-label="Account" data-menu-panel="" tabIndex={-1} onKeyDown={onMainKeyDown} className="outline-none">
                  <div role="none" className="mb-1 border-b border-line px-2.5 pb-2 pt-1">
                    <p className="truncate text-sm font-semibold text-fg" title={name}>
                      {name}
                    </p>
                    {email ? (
                      <p title={email} data-testid="account-email" className="truncate text-[11px] leading-4 text-fg-subtle">
                        {email}
                      </p>
                    ) : null}
                  </div>
                  {parentItem("notifications", Bell, "Notifications", <CountBadge count={unread} />, "shell-notifications")}
                  {showTours ? parentItem("tours", Compass, "Walkthrough guide", undefined, "shell-tour-launcher") : null}
                  {parentItem(
                    "theme",
                    Palette,
                    "Theme",
                    <span className="shrink-0 text-[11px] text-fg-subtle">{THEME_LABEL[mode]}</span>,
                  )}
                  <div role="separator" className="my-1 h-px bg-line" />
                  <button
                    type="button"
                    role="menuitem"
                    tabIndex={-1}
                    onClick={signOut}
                    aria-disabled={signingOut || undefined}
                    aria-busy={signingOut || undefined}
                    className={`${subItem} hover:bg-status-danger-bg hover:text-status-danger-fg ${signingOut ? "cursor-wait opacity-60" : ""}`}
                  >
                    {signingOut ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : (
                      <LogOut className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                    )}
                    <span className="font-medium">{signingOut ? "Signing out…" : "Sign out"}</span>
                  </button>
                </div>
              ) : (
                <div
                  ref={subRef}
                  role="menu"
                  aria-label={level === "notifications" ? "Notifications" : level === "tours" ? "Walkthrough guide" : "Theme"}
                  data-menu-panel=""
                  tabIndex={-1}
                  onKeyDown={onSubKeyDown}
                  className="outline-none"
                >
                  {level === "notifications" ? (
                    <>
                      {backItem("Notifications")}
                      <div className="border-t border-line">
                        <NotificationList items={notifications} isUnread={isUnread} onNavigate={leaveMenu} itemRole="menuitem" />
                      </div>
                      {notifications.length > 0 || canMonitor ? (
                        <div role="none" className="flex items-center justify-between gap-2 border-t border-line px-1 pt-1.5">
                          {notifications.length > 0 ? (
                            <MarkAllReadButton onClick={markAllRead} disabled={unread === 0} role="menuitem" />
                          ) : (
                            <span />
                          )}
                          {canMonitor ? (
                            <Link
                              href={MONITORING_HREF}
                              role="menuitem"
                              tabIndex={-1}
                              onClick={leaveMenu}
                              className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium text-fg-muted transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`}
                            >
                              <Gauge className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                              View monitoring
                            </Link>
                          ) : null}
                        </div>
                      ) : null}
                    </>
                  ) : null}
                  {level === "tours" ? (
                    <>
                      {backItem("Walkthrough guide")}
                      {choices.map((choice) => (
                        <button key={choice.id} type="button" role="menuitem" tabIndex={-1} className={subItem} onClick={() => launchTour(choice)}>
                          <choice.icon className="h-4 w-4 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={1.75} aria-hidden="true" />
                          <span className="min-w-0">
                            <span className="block font-medium text-fg">{choice.label}</span>
                            <span className="block truncate text-[11px] text-fg-subtle">{choice.description}</span>
                          </span>
                        </button>
                      ))}
                    </>
                  ) : null}
                  {level === "theme" ? (
                    <>
                      {backItem("Theme")}
                      {THEME_OPTIONS.map((option) => {
                        const checked = mode === option.mode;
                        return (
                          <button
                            key={option.mode}
                            type="button"
                            role="menuitemradio"
                            aria-checked={checked}
                            tabIndex={-1}
                            onClick={() => setTheme(option.mode)}
                            className={`${subItem} ${checked ? "font-semibold text-fg" : ""}`}
                          >
                            <option.icon className={itemIcon} strokeWidth={1.75} aria-hidden="true" />
                            <span className="flex-1">{option.label}</span>
                            {checked ? <Check className="h-3.5 w-3.5 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={2} aria-hidden="true" /> : null}
                          </button>
                        );
                      })}
                    </>
                  ) : null}
                </div>
              )}
            </m.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
