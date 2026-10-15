"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, m } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import {
  ChevronLeft,
  ChevronDown,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  FileText,
  Users,
  MessagesSquare,
  ThumbsUp,
  Shield,
  ShieldCheck,
  CircleDollarSign,
  Sparkles,
  ChartColumnBig,
  UserCog,
  Settings2,
  X,
} from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { crossfade, drawerLeft, layoutSpring, useMotionVariants } from "@/lib/motion";
import { useMobileNav } from "@/lib/mobile-nav-context";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { readStorage, writeStorage } from "@/lib/storage";
import { focusRing, iconButton, overlayBackdrop } from "@/components/ui/styles";
import { RoleBadge } from "@/components/shell/RoleBadge";
import { hasPermission, type Permission } from "@/lib/permissions";
import { usePendingAccessCount } from "@/lib/use-shell-notifications";
import { pop } from "@/lib/motion";

const COLLAPSED_STORAGE_KEY = "deployiq.sidebar.collapsed";
const ADMIN_EXPANDED_STORAGE_KEY = "deployiq.sidebar.adminExpanded";

/** Count badges a nav item can show (fed by the access-request count). */
export type NavBadgeKey = "pendingAccess";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  /** False pre-registers a page that is not built yet: it is not rendered. */
  enabled?: boolean;
  /**
   * Permission the signed-in role needs to see the item (newplan §7.3). Items
   * without one (Chat) are open to every signed-in, non-suspended user.
   */
  permission?: Permission;
  badgeKey?: NavBadgeKey;
  /** Extra palette search terms. */
  keywords?: string[];
}

const DASHBOARD: Permission = "dashboard:read";

const ADMIN_ITEMS: NavItem[] = [
  { href: "/admin", label: "Dashboard", icon: LayoutDashboard, permission: DASHBOARD },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/documents", label: "Documents", icon: FileText, permission: DASHBOARD },
  // Renamed from "Users" (newplan open item 6): these are chat visitors, not accounts.
  { href: "/admin/users", label: "Visitors", icon: Users, permission: DASHBOARD, keywords: ["users", "audience"] },
  { href: "/admin/usage", label: "Usage & Cost", icon: CircleDollarSign, permission: DASHBOARD },
  { href: "/admin/conversations", label: "Conversation Logs", icon: MessagesSquare, permission: DASHBOARD },
  { href: "/admin/feedback", label: "Feedback", icon: ThumbsUp, permission: DASHBOARD },
  { href: "/admin/evaluation-live", label: "Evaluation (Live)", icon: Sparkles, permission: DASHBOARD },
  { href: "/admin/governance", label: "Governance", icon: ShieldCheck, permission: DASHBOARD },
];

/** Pages from newplan §6, §7.8 and §14; all built and enabled in this preview. */
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
    permission: "branding:write",
    keywords: ["branding", "taxonomy", "white-label"],
  },
];

/** Every registered nav item, including disabled ones (used by the command palette). */
export const NAV_ITEMS: readonly NavItem[] = [...ADMIN_ITEMS, ...PLANNED_ITEMS];

export const isNavItemEnabled = (item: NavItem) => item.enabled !== false;

/** Enabled and allowed for the signed-in role (deny by default). */
export function canSeeNavItem(item: NavItem, permissions: readonly string[] | null | undefined): boolean {
  return isNavItemEnabled(item) && (!item.permission || hasPermission(permissions, item.permission));
}

/** The items a user with `permissions` sees, in sidebar order. */
export function visibleNavItems(permissions: readonly string[] | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => canSeeNavItem(item, permissions));
}

function initials(name: string | undefined | null) {
  return (name ?? "")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function isActive(pathname: string | null, href: string) {
  // Segment-aware so "/admin/evaluation" is not active on "/admin/evaluation-live".
  return pathname === href || (href !== "/admin" && Boolean(pathname?.startsWith(`${href}/`)));
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
  return (
    <>
      {items.filter(isNavItemEnabled).map((item) => {
        const active = isActive(pathname, item.href);
        const Icon = item.icon;
        const badge = item.badgeKey ? badges?.[item.badgeKey] : undefined;
        return (
          <Link
            key={item.href}
            href={item.href}
            title={collapsed ? item.label : undefined}
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
            className={`group relative flex items-center gap-2.5 rounded-lg py-1.5 text-[13px] font-medium transition-colors ${focusRing} ${
              collapsed ? "justify-center px-0" : "px-3"
            } ${
              active
                ? "bg-brand-50 dark:bg-brand-950/60 text-brand-700 dark:text-brand-300 font-semibold"
                : "text-ink/60 dark:text-slate-400 hover:bg-ink/[0.04] dark:hover:bg-white/5 hover:text-ink dark:hover:text-white"
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
  const { user, logout, role, permissions } = useAuth();
  const pendingAccess = usePendingAccessCount();
  const badges = badgeOverride ?? { pendingAccess };
  const items = visibleNavItems(permissions);

  // Read synchronously on mount (not in useState initializer) so server
  // and first client render both start expanded, avoiding a hydration
  // mismatch — the stored preference is then applied right after mount.
  const [collapsed, setCollapsed] = useState(false);
  // Controls whether the Admin section's items are expanded (shown) or
  // collapsed (hidden) when its header is clicked.
  const [adminExpanded, setAdminExpanded] = useState(true);

  useEffect(() => {
    setCollapsed(readStorage(COLLAPSED_STORAGE_KEY) === "1");
    setAdminExpanded(readStorage(ADMIN_EXPANDED_STORAGE_KEY) !== "0");
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      writeStorage(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  function toggleAdminExpanded() {
    setAdminExpanded((prev) => {
      const next = !prev;
      writeStorage(ADMIN_EXPANDED_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  return (
    <>
      <aside
        className={`hidden md:flex h-screen shrink-0 flex-col border-r border-ink/10 dark:border-white/10 bg-white dark:bg-dark-surface transition-[width] duration-200 ${
          collapsed ? "w-16" : "w-64"
        }`}
      >
        <div
          className={`flex h-16 shrink-0 items-center border-b border-ink/10 dark:border-white/10 ${
            collapsed ? "justify-center px-1" : "justify-between pl-4 pr-3"
          }`}
        >
          {!collapsed ? (
            <>
              <div className="flex flex-1 items-center min-w-0 h-full">
                <BrandLogo variant="full" />
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

        <nav aria-label="Main" className={`flex-1 space-y-0.5 overflow-y-auto py-4 ${collapsed ? "px-2" : "px-3"}`}>
          {collapsed ? (
            <NavList items={items} pathname={pathname} collapsed badges={badges} layoutId="sidebar-active" />
          ) : (
            <>
              <button
                onClick={toggleAdminExpanded}
                aria-expanded={adminExpanded}
                className={`flex w-full items-center justify-between rounded-md px-3 pb-2 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink/40 dark:text-slate-400 hover:text-ink/70 dark:hover:text-slate-200 ${focusRing}`}
              >
                <span className="flex items-center gap-1.5">
                  <Shield className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" />
                  <span>Admin</span>
                </span>
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${adminExpanded ? "" : "-rotate-90"}`}
                />
              </button>
              {adminExpanded && (
                <NavList
                  items={items}
                  pathname={pathname}
                  collapsed={false}
                  badges={badges}
                  layoutId="sidebar-active"
                />
              )}
            </>
          )}
        </nav>

        <div className="border-t border-ink/10 dark:border-white/10 p-3">
          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <ThemeToggle size="sm" />
              <div
                title={user?.full_name ?? "Signed out"}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white"
              >
                {user ? initials(user.full_name) : "—"}
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 rounded-lg px-2 py-1">
              <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white"
              >
                {user ? initials(user.full_name) : "—"}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink dark:text-slate-100">
                  {user?.full_name ?? "Signed out"}
                </p>
                <p className="truncate text-xs text-ink/50 dark:text-slate-400">{user?.email}</p>
                {role ? (
                  <div className="mt-1">
                    <RoleBadge role={role} />
                  </div>
                ) : null}
              </div>
              <div className="flex items-center gap-1">
                <ThemeToggle size="sm" />
                <button
                  onClick={() => void logout()}
                  title="Sign out"
                  aria-label="Sign out"
                  className={`rounded-md p-1.5 text-ink/40 dark:text-slate-400 hover:bg-ink/5 dark:hover:bg-white/10 hover:text-ink dark:hover:text-white transition ${focusRing}`}
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
      <MobileSidebar pathname={pathname} items={items} badges={badges} />
    </>
  );
}

/** Off-canvas sidebar below `md` (newplan §15.1), opened by the header hamburger. */
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
        <div key="mobile-nav" className="fixed inset-0 z-[120] md:hidden">
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
            <div className="flex h-16 shrink-0 items-center justify-between border-b border-line pl-4 pr-3">
              <BrandLogo variant="full" />
              <button type="button" onClick={() => setOpen?.(false)} aria-label="Close navigation" className={iconButton}>
                <X className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
              </button>
            </div>
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
          </m.aside>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
