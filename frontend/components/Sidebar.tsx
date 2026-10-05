"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";

const COLLAPSED_STORAGE_KEY = "deployiq.sidebar.collapsed";

const NAV_ITEMS = [
  {
    href: "/chat",
    label: "Chat",
    icon: (
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    ),
  },
  {
    href: "/documents",
    label: "Documents",
    icon: (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </>
    ),
  },
  {
    href: "/metrics",
    label: "Latency",
    icon: <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />,
  },
  {
    href: "/governance",
    label: "Governance",
    icon: <path d="M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6l8-4z" />,
  },
];

function initials(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function CollapseIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className={`transition-transform ${collapsed ? "rotate-180" : ""}`}
    >
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  // Read synchronously on mount (not in useState initializer) so server
  // and first client render both start expanded, avoiding a hydration
  // mismatch — the stored preference is then applied right after mount.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1");
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

  return (
    <aside
      className={`flex h-screen shrink-0 flex-col border-r border-ink/10 bg-white transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-60"
      }`}
    >
      <div className="flex h-16 items-center justify-between border-b border-ink/10 px-3">
        {!collapsed && (
          <Image src="/exigotech-logo.png" alt="Exigo Tech" width={128} height={31} />
        )}
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink/40 transition hover:bg-ink/5 hover:text-ink ${
            collapsed ? "mx-auto" : ""
          }`}
        >
          <CollapseIcon collapsed={collapsed} />
        </button>
      </div>

      {!collapsed && (
        <div className="px-5 pt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/40">
            DeployIQ
          </p>
        </div>
      )}

      <nav className={`flex-1 space-y-1 pt-3 ${collapsed ? "px-2" : "px-3"}`}>
        {NAV_ITEMS.map((item) => {
          const active = pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              className={`flex items-center gap-2.5 rounded-lg py-2 text-sm font-medium transition ${
                collapsed ? "justify-center px-0" : "px-3"
              } ${
                active
                  ? "bg-brand-50 text-brand-700"
                  : "text-ink/60 hover:bg-ink/5 hover:text-ink"
              }`}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="shrink-0"
              >
                {item.icon}
              </svg>
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );
        })}
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
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
