"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { AnimatePresence, m } from "motion/react";
import { X } from "lucide-react";
import { useBrand } from "@/lib/brand-context";
import { useChat } from "@/lib/chat-context";
import { isAskShortcut } from "@/lib/shortcuts";
import {
  greeting as greetingVariants,
  greetingDelayMs,
  mascotFloat,
  mascotHover,
  mascotTap,
  pulseRing,
  useMotionVariants,
  usePrefersReducedMotion,
} from "@/lib/motion";
import { dismissGreeting, isChatRoute, markGreetingSeen, shouldShowGreeting } from "@/lib/askdiq-widget";
import { focusRing } from "@/components/ui/styles";
import { AskDiqMascot } from "@/components/askdiq/AskDiqMascot";

/**
 * The drawer (conversation, history, composer) is code-split: the launcher
 * ships only this button, and the drawer chunk loads on first hover / focus /
 * open, so it adds next to nothing to every dashboard page's initial JS.
 */
const loadDrawer = () => import("@/components/askdiq/AskDiqDrawer").then((mod) => mod.AskDiqDrawer);
const AskDiqDrawer = dynamic(loadDrawer, { ssr: false });
const preloadDrawer = () => {
  void loadDrawer();
};

/**
 * Floating AskDIQ launcher on every dashboard page (bottom-right; the nav is
 * on the left, so the drawer opens from the right). Hidden on /chat, which
 * already renders the same conversation full-size — a second copy there
 * would just duplicate the page. State lives in ChatProvider, so a question
 * asked here keeps answering while the user moves between pages.
 */
export function AskDiqLauncher() {
  const pathname = usePathname();
  const hidden = isChatRoute(pathname);
  const { assistantName } = useBrand();
  const { sending } = useChat();
  const reduced = usePrefersReducedMotion();
  const bubble = useMotionVariants(greetingVariants);
  const drawerId = useId();

  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [unread, setUnread] = useState(false);
  const [greetingOpen, setGreetingOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const wasSending = useRef(sending);

  const openDrawer = useCallback(() => {
    setLoaded(true);
    setOpen(true);
    setUnread(false);
    setGreetingOpen(false);
    dismissGreeting();
  }, []);

  const closeDrawer = useCallback(() => {
    setOpen(false);
    buttonRef.current?.focus();
  }, []);

  // An answer that lands while the drawer is closed lights the unread dot.
  useEffect(() => {
    if (wasSending.current && !sending && !open && !hidden) setUnread(true);
    wasSending.current = sending;
  }, [sending, open, hidden]);

  // Navigating to /chat (e.g. "Open full chat") hands the conversation over.
  useEffect(() => {
    if (hidden) {
      setOpen(false);
      setUnread(false);
      setGreetingOpen(false);
    }
  }, [hidden]);

  // Once-per-session greeting, until dismissed for good.
  useEffect(() => {
    if (hidden || !shouldShowGreeting()) return undefined;
    const timer = window.setTimeout(() => {
      markGreetingSeen();
      setGreetingOpen(true);
    }, greetingDelayMs);
    return () => window.clearTimeout(timer);
  }, [hidden]);

  useEffect(() => {
    if (hidden) return undefined;
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || !isAskShortcut(event)) return;
      event.preventDefault();
      if (open) closeDrawer();
      else openDrawer();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [hidden, open, openDrawer, closeDrawer]);

  const animate = !reduced && !open;
  const label = `Ask ${assistantName}`;
  const status = unread ? "new answer" : sending && !open ? "answering" : null;

  return (
    <>
      {hidden ? null : (
        <div
          className="pointer-events-none fixed bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-4 z-[80] flex flex-col items-end gap-3 sm:right-6"
          data-testid="askdiq-launcher"
        >
          <AnimatePresence>
            {greetingOpen && !open ? (
              <m.div
                key="greeting"
                role="status"
                variants={bubble}
                initial="hidden"
                animate="visible"
                exit="exit"
                style={{ transformOrigin: "bottom right" }}
                className="pointer-events-auto relative flex max-w-[16rem] items-start gap-2 rounded-2xl rounded-br-md border border-line-brand/70 bg-surface-elevated py-2.5 pl-3.5 pr-2 text-sm text-fg shadow-studio dark:border-brand-500/25"
              >
                <button type="button" onClick={openDrawer} className={`rounded-md text-left leading-snug ${focusRing}`}>
                  Hi, I&apos;m <span className="font-semibold text-brand-700 dark:text-brand-300">{assistantName}</span> — ask me anything
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setGreetingOpen(false);
                    dismissGreeting();
                  }}
                  aria-label="Dismiss greeting"
                  className={`-mr-0.5 rounded-md p-0.5 text-fg-subtle transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`}
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                </button>
              </m.div>
            ) : null}
          </AnimatePresence>

          <div className="group pointer-events-auto relative">
            <span
              role="tooltip"
              id={`${drawerId}-tip`}
              className="pointer-events-none absolute right-full top-1/2 mr-3 hidden -translate-y-1/2 items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-surface-elevated px-2.5 py-1.5 text-xs font-medium text-fg opacity-0 shadow-lg transition-opacity duration-150 group-focus-within:opacity-100 group-hover:opacity-100 motion-reduce:transition-none sm:flex"
            >
              {label}
              <kbd className="rounded border border-line bg-surface-muted px-1 font-sans text-[10px] text-fg-subtle">Ctrl /</kbd>
            </span>

            {animate ? (
              <m.span
                aria-hidden="true"
                className="absolute inset-0 rounded-full bg-brand-400/50 dark:bg-brand-400/40"
                animate={pulseRing.animate}
                transition={pulseRing.transition}
              />
            ) : null}

            <m.button
              ref={buttonRef}
              type="button"
              data-tour="shell-chat-widget"
              onClick={open ? closeDrawer : openDrawer}
              onPointerEnter={preloadDrawer}
              onFocus={preloadDrawer}
              aria-label={status ? `${label} (${status})` : label}
              aria-describedby={`${drawerId}-tip`}
              aria-expanded={open}
              aria-controls={loaded ? drawerId : undefined}
              aria-haspopup="dialog"
              aria-keyshortcuts="Control+/ Meta+/"
              data-motion={reduced ? "static" : "animated"}
              whileHover={reduced ? undefined : mascotHover}
              whileTap={reduced ? undefined : mascotTap}
              className={`relative flex h-16 w-16 items-center justify-center rounded-full border border-line-brand bg-surface-card shadow-studio ring-4 ring-brand-400/15 transition-shadow hover:shadow-studio-hover dark:border-brand-500/30 dark:ring-brand-400/10 ${focusRing}`}
            >
              <m.span
                className="flex"
                animate={animate ? mascotFloat.animate : undefined}
                transition={animate ? mascotFloat.transition : undefined}
              >
                <AskDiqMascot size={52} />
              </m.span>

              {unread ? (
                <span
                  data-testid="askdiq-unread"
                  className="absolute right-0.5 top-0.5 h-3.5 w-3.5 rounded-full border-2 border-surface-card bg-brand-500"
                  aria-hidden="true"
                />
              ) : sending && !open ? (
                <span
                  data-testid="askdiq-typing"
                  className="absolute -top-1 right-[-0.35rem] flex items-center gap-0.5 rounded-full border border-line-brand bg-surface-elevated px-1.5 py-1 shadow-sm"
                  aria-hidden="true"
                >
                  <span className="h-1 w-1 rounded-full bg-brand-500 motion-safe:animate-bounce motion-safe:[animation-delay:-0.3s]" />
                  <span className="h-1 w-1 rounded-full bg-brand-500 motion-safe:animate-bounce motion-safe:[animation-delay:-0.15s]" />
                  <span className="h-1 w-1 rounded-full bg-brand-500 motion-safe:animate-bounce" />
                </span>
              ) : null}
            </m.button>
          </div>
        </div>
      )}

      {loaded ? <AskDiqDrawer id={drawerId} open={open && !hidden} onClose={closeDrawer} /> : null}
    </>
  );
}
