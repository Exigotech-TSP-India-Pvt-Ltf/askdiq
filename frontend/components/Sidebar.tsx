"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
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
  ClipboardCheck,
  Shield,
  ShieldCheck,
  CircleDollarSign,
  Sparkles,
} from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";

const COLLAPSED_STORAGE_KEY = "deployiq.sidebar.collapsed";
const ADMIN_EXPANDED_STORAGE_KEY = "deployiq.sidebar.adminExpanded";

const ADMIN_ITEMS = [
  { href: "/admin", label: "Dashboard", icon: LayoutDashboard },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/usage", label: "Usage & Cost", icon: CircleDollarSign },
  { href: "/admin/conversations", label: "Conversation Logs", icon: MessagesSquare },
  { href: "/admin/feedback", label: "Feedback", icon: ThumbsUp },
  { href: "/admin/evaluation", label: "Evaluation", icon: ClipboardCheck },
  { href: "/admin/evaluation-live", label: "Evaluation (Live)", icon: Sparkles },
  { href: "/admin/governance", label: "Admin Governance", icon: ShieldCheck },
];

function initials(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  // Read synchronously on mount (not in useState initializer) so server
  // and first client render both start expanded, avoiding a hydration
  // mismatch — the stored preference is then applied right after mount.
  const [collapsed, setCollapsed] = useState(false);
  // Controls whether the Admin section's items are expanded (shown) or
  // collapsed (hidden) when its header is clicked.
  const [adminExpanded, setAdminExpanded] = useState(true);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1");
      setAdminExpanded(window.localStorage.getItem(ADMIN_EXPANDED_STORAGE_KEY) !== "0");
    } catch {
      // Ignore localStorage errors (e.g. disabled in this browser).
    }
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // Ignore localStorage errors.
      }
      return next;
    });
  }

  function toggleAdminExpanded() {
    setAdminExpanded((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(ADMIN_EXPANDED_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // Ignore localStorage errors.
      }
      return next;
    });
  }

  function renderLink(item: { href: string; label: string; icon: React.ElementType }, isAdminRoot: boolean) {
    const active = isAdminRoot
      ? pathname === item.href || (item.href !== "/admin" && pathname?.startsWith(item.href))
      : pathname?.startsWith(item.href);
    const Icon = item.icon;
    return (
      <Link
        key={item.href}
        href={item.href}
        title={collapsed ? item.label : undefined}
        className={`group relative flex items-center gap-2.5 rounded-lg py-1.5 text-[13px] font-medium transition-colors ${
          collapsed ? "justify-center px-0" : "px-3"
        } ${
          active
            ? "bg-brand-50 dark:bg-brand-950/60 text-brand-700 dark:text-brand-300 font-semibold"
            : "text-ink/60 dark:text-slate-400 hover:bg-ink/[0.04] dark:hover:bg-white/5 hover:text-ink dark:hover:text-white"
        }`}
      >
        {active && !collapsed && (
          <span className="absolute left-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-r-full bg-brand-600" />
        )}
        <Icon className={`h-4 w-4 shrink-0 ${active ? "text-brand-600 dark:text-brand-400" : "text-ink/40 dark:text-slate-500 group-hover:text-ink/70 dark:group-hover:text-slate-300"}`} strokeWidth={1.8} />
        {!collapsed && <span className="truncate">{item.label}</span>}
      </Link>
    );
  }

  return (
    <aside
      className={`flex h-screen shrink-0 flex-col border-r border-ink/10 dark:border-white/10 bg-white dark:bg-dark-surface transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-64"
      }`}
    >
      <div
        className={`flex h-20 items-center border-b border-ink/10 dark:border-white/10 ${
          collapsed ? "justify-center px-1" : "justify-between pl-4 pr-3"
        }`}
      >
        {!collapsed ? (
          <>
            <div className="flex flex-1 items-center min-w-0 h-full py-2">
              <img
                src="/exigotech-logo.svg"
                alt="Exigo Tech"
                className="h-[64px] max-h-[66px] w-auto max-w-[191px] object-contain object-left dark:brightness-110 transition-all"
              />
            </div>
            <button
              onClick={toggleCollapsed}
              title="Collapse sidebar"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink/40 dark:text-slate-400 transition hover:bg-ink/5 dark:hover:bg-white/10 hover:text-ink dark:hover:text-white"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </>
        ) : (
          <button
            onClick={toggleCollapsed}
            title="Expand sidebar"
            className="group flex h-10 w-10 items-center justify-center rounded-lg p-1 transition hover:bg-ink/5 dark:hover:bg-white/10"
          >
            <img src="/exigotech-mark.svg" alt="Exigo Tech" className="h-7 w-7 object-contain" />
          </button>
        )}
      </div>

      <nav className={`flex-1 space-y-0.5 overflow-y-auto py-4 ${collapsed ? "px-2" : "px-3"}`}>
        {collapsed ? (
          ADMIN_ITEMS.map((item) => renderLink(item, true))
        ) : (
          <>
            <button
              onClick={toggleAdminExpanded}
              className="flex w-full items-center justify-between rounded-md px-3 pb-2 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink/40 dark:text-slate-400 hover:text-ink/70 dark:hover:text-slate-200"
            >
              <span className="flex items-center gap-1.5">
                <Shield className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" />
                <span>Admin</span>
              </span>
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${adminExpanded ? "" : "-rotate-90"}`}
              />
            </button>
            {adminExpanded && ADMIN_ITEMS.map((item) => renderLink(item, true))}
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
            </div>
            <div className="flex items-center gap-1">
              <ThemeToggle size="sm" />
              <button
                onClick={logout}
                title="Sign out"
                className="rounded-md p-1.5 text-ink/40 dark:text-slate-400 hover:bg-ink/5 dark:hover:bg-white/10 hover:text-ink dark:hover:text-white transition"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

