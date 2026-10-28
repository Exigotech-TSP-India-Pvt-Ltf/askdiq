"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, m } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { prefetchRouteData } from "@/lib/route-prefetch";
import {
  ChevronLeft,
  LayoutDashboard,
  BotMessageSquare,
  LibraryBig,
  FlaskConical,
  Users,
  ThumbsUp,
  ShieldCheck,
  CircleDollarSign,
  ChartColumnBig,
  UserCog,
  Settings2,
  X,
} from "lucide-react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { crossfade, drawerLeft, layoutSpring, pop, useMotionVariants } from "@/lib/motion";
import { useMobileNav } from "@/lib/mobile-nav-context";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { readStorage, writeStorage } from "@/lib/storage";
import { focusRing, iconButton, overlayBackdrop } from "@/components/ui/styles";
import { useCommandCenter } from "@/components/shell/command-center-context";
import { GoToPageButton } from "@/components/shell/GoToPageButton";
import { SidebarAccount } from "@/components/shell/SidebarAccount";
import { hasPermission, type Permission } from "@/lib/permissions";
import { usePendingAccessCount } from "@/lib/use-shell-notifications";

const COLLAPSED_STORAGE_KEY = "deployiq.sidebar.collapsed";
/** Sidebar logo: width-led, capped at the 56px box the padded h-20 row leaves. */
export const SIDEBAR_LOGO_SIZE = "max-h-14 h-auto w-auto max-w-[200px] object-contain object-left";
/** Mobile drawer logo: the padded h-16 row leaves a 40px box. */
export const DRAWER_LOGO_SIZE = "max-h-10 h-auto w-auto max-w-[200px] object-contain object-left";

/** Count badges a nav item can show (fed by the access-request count). */
export type NavBadgeKey = "pendingAccess";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  /** False pre-registers a page that is not built yet: it is not rendered. */
  enabled?: boolean;
  /**
   * Permission the signed-in role needs to see the item. Items
   * without one (AskDIQ) are open to every signed-in, non-suspended user.
   */
  permission?: Permission;
  /** Visible with any one of these (when the page itself accepts either). */
  anyPermission?: readonly Permission[];
  badgeKey?: NavBadgeKey;
  /** Extra palette search terms. */
  keywords?: string[];
  /** Other path prefixes that highlight this item (e.g. detail pages under old URLs). */
  activePrefixes?: readonly string[];
}

const DASHBOARD: Permission = "dashboard:read";

const ADMIN_ITEMS: NavItem[] = [
  { href: "/admin", label: "Dashboard", icon: LayoutDashboard, permission: DASHBOARD },
  { href: "/chat", label: "AskDIQ", icon: BotMessageSquare, keywords: ["chat", "assistant", "ask"] },
  { href: "/documents", label: "Knowledge", icon: LibraryBig, permission: DASHBOARD, keywords: ["documents", "knowledge base", "upload"] },
  // Visitors (chat visitors by IP, not accounts) and Conversation Logs are one
  // tabbed page; their detail pages keep their own URLs and highlight this item.
  {
    href: "/admin/visitors",
    label: "Visitors & Conversations",
    icon: Users,
    permission: DASHBOARD,
    activePrefixes: ["/admin/users", "/admin/conversations"],
    keywords: ["visitors", "users", "audience", "ip", "test ip", "conversations", "conversation logs", "sessions", "transcripts"],
  },
  { href: "/admin/usage", label: "Usage & Cost", icon: CircleDollarSign, permission: DASHBOARD },
  { href: "/admin/feedback", label: "Feedback", icon: ThumbsUp, permission: DASHBOARD },
  { href: "/admin/evaluation-live", label: "Evaluation", icon: FlaskConical, permission: DASHBOARD, keywords: ["quality", "live evaluation", "rag"] },
  { href: "/admin/governance", label: "Governance", icon: ShieldCheck, permission: DASHBOARD },
];

/** Analytics, User Management and Branding pages; all built and enabled in this preview. */
const PLANNED_ITEMS: NavItem[] = [
  { href: "/admin/analytics", label: "Analytics", icon: ChartColumnBig, permission: DASHBOARD },
  {
    href: "/admin/user-management",
    label: "User Management",
    icon: UserCog,
    permission: "users:manage",
    badgeKey: "pendingAccess",
    keywords: ["access requests", "roles"],
  },
  {
    href: "/admin/settings",
    label: "Settings",
    icon: Settings2,
    // The page opens with any permitted Settings section.
    anyPermission: ["branding:write", "taxonomy:write", "models:write", "knowledge:links", "testip:write"],
    keywords: ["branding", "taxonomy", "white-label", "models", "llm", "embeddings", "pricing", "cost", "test ip"],
  },
];

/** Every registered nav item, including disabled ones (used by the command palette). */
export const NAV_ITEMS: readonly NavItem[] = [...ADMIN_ITEMS, ...PLANNED_ITEMS];

export const isNavItemEnabled = (item: NavItem) => item.enabled !== false;

/** Enabled and allowed for the signed-in role (deny by default). */
export function canSeeNavItem(item: NavItem, permissions: readonly string[] | null | undefined): boolean {
  if (!isNavItemEnabled(item)) return false;
  if (item.anyPermission) return item.anyPermission.some((p) => hasPermission(permissions, p));
  return !item.permission || hasPermission(permissions, item.permission);
}

/** The items a user with `permissions` sees, in sidebar order. */
export function visibleNavItems(permissions: readonly string[] | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => canSeeNavItem(item, permissions));
}

function matchesPrefix(pathname: string | null, href: string) {
  // Segment-aware so "/admin/evaluation" is not active on "/admin/evaluation-live".
  return pathname === href || (href !== "/admin" && Boolean(pathname?.startsWith(`${href}/`)));
}

export function isNavItemActive(pathname: string | null, item: Pick<NavItem, "href" | "activePrefixes">): boolean {
  return matchesPrefix(pathname, item.href) || (item.activePrefixes ?? []).some((p) => matchesPrefix(pathname, p));
}

interface NavListProps {
  items: readonly NavItem[];
  pathname: string | null;
  collapsed: boolean;
  badges?: Partial<Record<NavBadgeKey, number>>;
  /** Distinct per instance so desktop and mobile indicators animate independently. */
  layoutId: string;
  onNavigate?: () => void;
}

function NavList({ items, pathname, collapsed, badges, layoutId, onNavigate }: NavListProps) {
  const badgeVariants = useMotionVariants(pop);
  const { token } = useAuth();
  const { audience } = useAudience();
  // Navigation intent: <Link> prefetches the route in production; also warm
  // the page's first queries (ConsoleRuntime preloads them all once idle).
  const warm = (href: string) => prefetchRouteData(href, token, audience);
  return (
    <>
      {items.filter(isNavItemEnabled).map((item) => {
        const active = isNavItemActive(pathname, item);
        const Icon = item.icon;
        const badge = item.badgeKey ? badges?.[item.badgeKey] : undefined;
        return (
          <Link
            key={item.href}
            href={item.href}
            // Full prefetch: the route payload is cached (next.config
            // staleTimes), so the click renders without a server round trip.
            prefetch
            title={collapsed ? item.label : undefined}
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
            onMouseEnter={() => warm(item.href)}
            onFocus={() => warm(item.href)}
            className={`group relative flex items-center gap-2.5 rounded-lg py-1.5 text-[13px] font-medium transition-colors ${focusRing} ${
              collapsed ? "justify-center px-0" : "px-3"
            } ${
              active
                ? "bg-brand-50 dark:bg-brand-950/60 text-brand-700 dark:text-brand-300 font-semibold"
                : "text-ink/60 dark:text-slate-400 hover:bg-ink/4 dark:hover:bg-white/5 hover:text-ink dark:hover:text-white"
            }`}
          >
            {active && !collapsed && (
              <m.span
                layoutId={layoutId}
                transition={layoutSpring}
                data-testid="nav-active-indicator"
                className="absolute left-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-full bg-brand-600"
              />
            )}
            <Icon
              className={`h-[18px] w-[18px] shrink-0 transition-colors ${active ? "text-brand-600 dark:text-brand-400" : "text-ink/40 dark:text-slate-500 group-hover:text-ink/70 dark:group-hover:text-slate-300"}`}
              strokeWidth={1.75}
            />
            {!collapsed && <span className="truncate">{item.label}</span>}
            <AnimatePresence initial={false}>
              {badge ? (
                <m.span
                  key={badge}
                  variants={badgeVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  aria-label={`${badge} pending`}
                  data-testid="nav-badge"
                  className={`rounded-full bg-status-warn px-1.5 text-[10px] font-semibold leading-4 text-white dark:text-fg-inverse ${
                    collapsed ? "absolute right-0.5 top-0" : "ml-auto"
                  }`}
                >
                  {badge > 99 ? "99+" : badge}
                </m.span>
              ) : null}
            </AnimatePresence>
          </Link>
        );
      })}
    </>
  );
}

interface SidebarProps {
  /** Overrides the nav badge counts (default: open access requests for `users:manage`). */
  badges?: Partial<Record<NavBadgeKey, number>>;
}

export function Sidebar({ badges: badgeOverride }: SidebarProps = {}) {
  const pathname = usePathname();
  const { permissions } = useAuth();
  const pendingAccess = usePendingAccessCount();
  const badges = badgeOverride ?? { pendingAccess };
  const items = visibleNavItems(permissions);

  // Read synchronously on mount (not in useState initializer) so server
  // and first client render both start expanded, avoiding a hydration
  // mismatch â€” the stored preference is then applied right after mount.
  const [collapsed, setCollapsed] = useState(false);
  const hasPalette = useCommandCenter() !== null;

  useEffect(() => {
    setCollapsed(readStorage(COLLAPSED_STORAGE_KEY) === "1");
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      writeStorage(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  return (
    <>
      <aside
        data-tour="shell-sidebar"
        className={`hidden md:flex h-screen shrink-0 flex-col border-r border-ink/10 dark:border-white/10 bg-surface-card transition-[width] duration-200 ${
          collapsed ? "w-16" : "w-64"
        }`}
      >
        <div
          data-testid="sidebar-logo-row"
          className={`flex h-20 shrink-0 items-center border-b border-ink/10 py-3 dark:border-white/10 ${
            collapsed ? "justify-center px-1" : "justify-between pl-4 pr-3"
          }`}
        >
          {!collapsed ? (
            <>
              <div className="flex flex-1 items-center min-w-0 h-full">
                {/* Width-led but height-bounded: the h-20 row with py-3 leaves a 56px box,
                    so a tall (e.g. 1:1 uploaded) logo never touches the borders. */}
                <BrandLogo variant="full" size={SIDEBAR_LOGO_SIZE} />
              </div>
              <button
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink/40 dark:text-slate-400 transition hover:bg-ink/5 dark:hover:bg-white/10 hover:text-ink dark:hover:text-white ${focusRing}`}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </>
          ) : (
            <button
              onClick={toggleCollapsed}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              className={`group flex h-10 w-10 items-center justify-center rounded-lg p-1 transition hover:bg-ink/5 dark:hover:bg-white/10 ${focusRing}`}
            >
              <BrandLogo variant="mark" />
            </button>
          )}
        </div>

        {hasPalette ? (
          <div className={`shrink-0 pt-3 ${collapsed ? "flex justify-center px-2" : "px-3"}`}>
            <GoToPageButton collapsed={collapsed} />
          </div>
        ) : null}

        <nav aria-label="Main" data-tour="shell-nav" className={`flex-1 space-y-0.5 overflow-y-auto py-4 ${collapsed ? "px-2" : "px-3"}`}>
          {/* One flat, permission-filtered list: no group headers. */}
          <NavList items={items} pathname={pathname} collapsed={collapsed} badges={badges} layoutId="sidebar-active" />
        </nav>

        <SidebarAccount collapsed={collapsed} />
      </aside>
      <MobileSidebar pathname={pathname} items={items} badges={badges} />
    </>
  );
}

/** Off-canvas sidebar below `md`, opened by the header hamburger. */
function MobileSidebar({
  pathname,
  items,
  badges,
}: {
  pathname: string | null;
  items: readonly NavItem[];
  badges?: Partial<Record<NavBadgeKey, number>>;
}) {
  const nav = useMobileNav();
  const open = nav?.open ?? false;
  const setOpen = nav?.setOpen;
  const panelRef = useRef<HTMLElement>(null);
  const variants = useMotionVariants(drawerLeft);
  const hasPalette = useCommandCenter() !== null;
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useFocusTrap(panelRef, open && mounted, { onEscape: () => setOpen?.(false) });

  // Close after navigating (the drawer must not cover the new page).
  useEffect(() => {
    setOpen?.(false);
  }, [pathname, setOpen]);

  if (!nav || !mounted) return null;

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div key="mobile-nav" className="fixed inset-0 z-120 md:hidden">
          <m.div
            aria-hidden="true"
            className={overlayBackdrop}
            variants={crossfade}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={() => setOpen?.(false)}
          />
          <m.aside
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            tabIndex={-1}
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="relative flex h-full w-72 max-w-[85vw] flex-col border-r border-line bg-surface-card shadow-2xl"
          >
            <div data-testid="drawer-logo-row" className="flex h-16 shrink-0 items-center justify-between border-b border-line py-3 pl-4 pr-3">
              <BrandLogo variant="full" size={DRAWER_LOGO_SIZE} />
              <button type="button" onClick={() => setOpen?.(false)} aria-label="Close navigation" className={iconButton}>
                <X className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
              </button>
            </div>
            {hasPalette ? (
              <div className="shrink-0 px-3 pt-3">
                <GoToPageButton onBeforeOpen={() => setOpen?.(false)} />
              </div>
            ) : null}
            <nav aria-label="Main" className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4">
              <NavList
                items={items}
                pathname={pathname}
                collapsed={false}
                badges={badges}
                layoutId="mobile-sidebar-active"
                onNavigate={() => setOpen?.(false)}
              />
            </nav>
            {/* Same account corner as the desktop sidebar: identity, notifications, tours, theme, sign out. */}
            <SidebarAccount collapsed={false} surface="drawer" onBeforeAction={() => setOpen?.(false)} />
          </m.aside>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
