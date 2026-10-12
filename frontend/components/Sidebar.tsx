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
  ShieldCheck,
  CircleDollarSign,
} from "lucide-react";

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
        className={`group relative flex items-center gap-3 rounded-lg py-2 text-sm font-medium transition-colors ${
          collapsed ? "justify-center px-0" : "px-3"
        } ${
          active
            ? "bg-brand-50 text-brand-700"
            : "text-ink/60 hover:bg-ink/[0.04] hover:text-ink"
        }`}
      >
        {active && !collapsed && (
          <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-brand-600" />
        )}
        <Icon className={`h-[18px] w-[18px] shrink-0 ${active ? "text-brand-600" : "text-ink/40 group-hover:text-ink/70"}`} strokeWidth={2} />
        {!collapsed && <span className="truncate">{item.label}</span>}
      </Link>
    );
  }

  return (
    <aside
      className={`flex h-screen shrink-0 flex-col border-r border-ink/10 bg-white transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-64"
      }`}
    >
      <div className="flex h-16 items-center justify-between border-b border-ink/10 px-4">
        {!collapsed && (
          <Image src="/exigotech-logo.png" alt="Exigo Tech" width={120} height={29} />
        )}
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink/40 transition hover:bg-ink/5 hover:text-ink ${
            collapsed ? "mx-auto" : ""
          }`}
        >
          <ChevronLeft className={`h-4 w-4 transition-transform ${collapsed ? "rotate-180" : ""}`} />
        </button>
      </div>

      <nav className={`flex-1 space-y-0.5 overflow-y-auto py-4 ${collapsed ? "px-2" : "px-3"}`}>
        {collapsed ? (
          ADMIN_ITEMS.map((item) => renderLink(item, true))
        ) : (
          <>
            <button
              onClick={toggleAdminExpanded}
              className="flex w-full items-center justify-between rounded-md px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-ink/35 hover:text-ink/60"
            >
              <span>Admin</span>
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${adminExpanded ? "" : "-rotate-90"}`}
              />
            </button>
            {adminExpanded && ADMIN_ITEMS.map((item) => renderLink(item, true))}
          </>
        )}
      </nav>

      <div className="border-t border-ink/10 p-3">
        <div
          className={`flex items-center rounded-lg py-2 ${
            collapsed ? "justify-center px-0" : "gap-3 px-2"
          }`}
        >
          <div
            title={collapsed ? (user?.full_name ?? "Signed out") : undefined}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white"
          >
            {user ? initials(user.full_name) : "—"}
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">
                {user?.full_name ?? "Signed out"}
              </p>
              <p className="truncate text-xs text-ink/50">{user?.email}</p>
            </div>
          )}
          {!collapsed && (
            <button
              onClick={logout}
              title="Sign out"
              className="rounded-md p-1.5 text-ink/40 hover:bg-ink/5 hover:text-ink"
            >
              <LogOut className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

