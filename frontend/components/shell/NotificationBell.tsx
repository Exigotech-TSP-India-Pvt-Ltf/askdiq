"use client";

import React, { useId } from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { Bell, BellOff } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import { usePopover } from "@/lib/use-popover";
import { EmptyState } from "@/components/ui/EmptyState";
import { focusRing, iconButton, popoverPanel } from "@/components/ui/styles";

export interface NotificationItem {
  id: string;
  title: string;
  description?: string;
  href?: string;
  /** Pre-formatted relative time, e.g. "5 min ago". */
  time?: string;
  tone?: "info" | "warn" | "danger";
}

interface NotificationBellProps {
  /** Access requests, CRITICAL incidents and feedback spikes (fed by SSE in PR-18). */
  items?: NotificationItem[];
}

const TONE_DOT: Record<NonNullable<NotificationItem["tone"]>, string> = {
  info: "bg-status-info",
  warn: "bg-status-warn",
  danger: "bg-status-danger",
};

/** Header notification bell (newplan §15.1). Shell only: shows an empty state until PR-18 feeds it. */
export function NotificationBell({ items = [] }: NotificationBellProps) {
  const { open, toggle, close, triggerRef, panelRef } = usePopover();
  const variants = useMotionVariants(pop);
  const panelId = useId();
  const count = items.length;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={count ? `Notifications (${count} new)` : "Notifications"}
        className={`${iconButton} relative border border-line bg-surface-card shadow-sm`}
      >
        <Bell className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        {count ? (
          <span className="absolute -right-1 -top-1 min-w-[1rem] rounded-full bg-status-danger px-1 text-center text-[10px] font-semibold leading-4 text-white dark:text-fg-inverse">
            {count > 9 ? "9+" : count}
          </span>
        ) : null}
      </button>
      <AnimatePresence>
        {open ? (
          <m.div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="Notifications"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`absolute right-0 z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] origin-top-right ${popoverPanel}`}
          >
            <p className="border-b border-line px-2.5 pb-2 pt-1 text-xs font-semibold text-fg">Notifications</p>
            {count === 0 ? (
              <EmptyState compact icon={BellOff} title="You're all caught up" description="Access requests and incidents will appear here." />
            ) : (
              <ul className="max-h-80 overflow-y-auto py-1">
                <AnimatePresence initial={false}>
                  {items.map((item) => {
                    const body = (
                      <>
                        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${TONE_DOT[item.tone ?? "info"]}`} aria-hidden="true" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-medium text-fg">{item.title}</span>
                          {item.description ? <span className="block truncate text-[11px] text-fg-muted">{item.description}</span> : null}
                        </span>
                        {item.time ? <span className="shrink-0 text-[10px] text-fg-subtle">{item.time}</span> : null}
                      </>
                    );
                    const rowClass = `flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-muted ${focusRing}`;
                    return (
                      <m.li key={item.id} variants={variants} initial="hidden" animate="visible" exit="exit">
                        {item.href ? (
                          <Link href={item.href} onClick={() => close(false)} className={rowClass}>
                            {body}
                          </Link>
                        ) : (
                          <div className={rowClass}>{body}</div>
                        )}
                      </m.li>
                    );
                  })}
                </AnimatePresence>
              </ul>
            )}
          </m.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
