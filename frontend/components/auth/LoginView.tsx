"use client";

import React from "react";
import { AnimatePresence, m, useMotionValue } from "motion/react";
import { AlertCircle } from "lucide-react";
import { BrandPanelDecor } from "@/components/auth/BrandPanelDecor";
import { MicrosoftSignInButton } from "@/components/auth/MicrosoftSignInButton";
import { useBrand } from "@/lib/brand-context";
import { LOGIN_LOGO_SRC, type Brand } from "@/lib/brand";
import { loginErrorMessage } from "@/lib/login-errors";
import { fadeUp, pop, stagger, useMotionVariants, usePrefersReducedMotion } from "@/lib/motion";
import { focusRing } from "@/components/ui/styles";

export interface LoginCopy {
  eyebrow: string;
  headline: string;
  subtext: string;
  description: string;
}

/** Brand copy with the DeployIQ defaults for any `login_*` field the backend does not send. */
export function loginCopy(brand: Brand): LoginCopy {
  return {
    eyebrow: brand.login.eyebrow ?? brand.assistantName.toUpperCase(),
    headline: brand.login.headline ?? "Governed answers for security and compliance teams.",
    subtext:
      brand.login.subtext ??
      `${brand.productName} answers from your own knowledge base, with every response traceable, reviewed and governed.`,
    description:
      brand.login.description ??
      `Sign in with your ${brand.companyName} Microsoft account to open the ${brand.productName} console.`,
  };
}

interface LoginViewProps {
  /** Raw `?error=` value: only ever mapped to an allow-listed message. */
  errorCode: string | null;
  /** Validated same-origin post-login path. */
  next: string | null;
  /** Server-provided ACCESS_CONTACT_EMAIL (already validated), or null. */
  contactEmail: string | null;
  year: number;
}

const linkClass = `rounded text-brand-700 underline-offset-4 hover:underline dark:text-brand-300 ${focusRing}`;

function FooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  const external = /^https:\/\//.test(href);
  return (
    <a
      href={href}
      className={`rounded text-fg-subtle transition-colors hover:text-fg ${focusRing}`}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

/** The all-white wordmark, shown only on the sign-in screen. */
function LoginLogo({ alt, className }: { alt: string; className: string }) {
  // eslint-disable-next-line @next/next/no-img-element -- SVG logo: next/image adds nothing here.
  return <img src={LOGIN_LOGO_SRC} alt={alt} className={`w-auto max-w-none drop-shadow-[0_2px_8px_rgba(0,0,0,0.25)] ${className}`} />;
}

/**
 * Branded split-screen sign-in (newplan §7.7 rev 3.2): teal brand panel with
 * animated hexagons on the left (hidden below md, replaced by a teal header
 * strip), the Microsoft SSO card on the right. Every string, URL and logo
 * comes from the brand.
 */
export function LoginView({ errorCode, next, contactEmail, year }: LoginViewProps) {
  const brand = useBrand();
  const copy = loginCopy(brand);
  const reduced = usePrefersReducedMotion();
  const item = useMotionVariants(fadeUp);
  const popVariants = useMotionVariants(pop);
  const error = loginErrorMessage(errorCode);
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const links = [
    { href: brand.links.privacy, label: "Privacy Policy" },
    { href: brand.links.terms, label: "Terms of Service" },
    { href: brand.links.help, label: "Help" },
  ].filter((link): link is { href: string; label: string } => Boolean(link.href));

  function onPointerMove(event: React.PointerEvent<HTMLElement>) {
    if (reduced) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    pointerX.set((event.clientX - rect.left) / rect.width - 0.5);
    pointerY.set((event.clientY - rect.top) / rect.height - 0.5);
  }

  function onPointerLeave() {
    pointerX.set(0);
    pointerY.set(0);
  }

  // Under reduced motion nothing animates: no variants are applied at all.
  const motionProps = reduced ? {} : { variants: stagger, initial: "hidden", animate: "visible" };
  const itemProps = reduced ? {} : { variants: item };

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg md:flex-row" data-motion={reduced ? "static" : "animated"}>
      {/* Mobile: teal header strip with the logo above the card. */}
      <header className="relative overflow-hidden bg-gradient-to-br from-brand-600 to-brand-800 px-6 py-5 md:hidden">
        <LoginLogo alt={brand.companyName} className="h-12" />
      </header>

      <aside
        aria-label={`${brand.productName} introduction`}
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        className="relative hidden flex-col justify-between overflow-hidden bg-gradient-to-br from-brand-600 to-brand-800 p-12 text-white md:flex md:w-[53%] lg:p-16"
      >
        {/* Soft radial highlight. */}
        <div aria-hidden="true" className="pointer-events-none absolute -left-24 -top-24 h-[28rem] w-[28rem] rounded-full bg-white/10 blur-3xl" />
        <BrandPanelDecor pointerX={pointerX} pointerY={pointerY} reduced={reduced} />

        <div className="relative">
          <LoginLogo alt={brand.companyName} className="h-20 lg:h-24" />
        </div>

        <m.div className="relative max-w-xl" {...motionProps}>
          <m.p {...itemProps} className="text-xs font-semibold uppercase tracking-[0.2em] text-white/80">
            {copy.eyebrow}
          </m.p>
          <m.h2 {...itemProps} className="mt-4 text-4xl font-bold leading-tight tracking-tight lg:text-5xl">
            {copy.headline}
          </m.h2>
          <m.p {...itemProps} className="mt-5 max-w-lg text-base leading-relaxed text-white/85">
            {copy.subtext}
          </m.p>
        </m.div>

        <p className="relative text-xs text-white/75">
          © {year} {brand.companyName}
        </p>
      </aside>

      <main className="flex flex-1 items-center justify-center px-6 py-10 sm:px-10">
        <m.div className="w-full max-w-md" {...motionProps}>
          <m.h1 {...itemProps} className="text-2xl font-bold tracking-tight text-fg sm:text-3xl">
            Welcome to {brand.productName}
          </m.h1>
          <m.p {...itemProps} className="mt-3 text-sm leading-relaxed text-fg-muted">
            {copy.description}
          </m.p>

          <AnimatePresence initial={false}>
            {error ? (
              <m.div
                key="login-error"
                role="alert"
                variants={popVariants}
                initial={reduced ? false : "hidden"}
                animate="visible"
                exit="exit"
                className="mt-6 flex items-start gap-2.5 rounded-xl border border-status-danger/30 bg-status-danger-bg px-3.5 py-3 text-sm text-status-danger-fg"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                <span>{error}</span>
              </m.div>
            ) : null}
          </AnimatePresence>

          <m.div {...itemProps} className="mt-8">
            <MicrosoftSignInButton next={next} />
          </m.div>

          <m.div {...itemProps} className="my-8 flex items-center gap-3" role="presentation">
            <span className="h-px flex-1 bg-line" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-fg-subtle">Single sign-on</span>
            <span className="h-px flex-1 bg-line" />
          </m.div>

          <m.p {...itemProps} className="text-sm leading-relaxed text-fg-muted">
            Access is provisioned by your {brand.companyName} administrator. If you can&apos;t sign in, contact{" "}
            {contactEmail ? (
              <a href={`mailto:${contactEmail}`} className={`font-medium ${linkClass}`}>
                {contactEmail}
              </a>
            ) : (
              "your administrator"
            )}
            .
          </m.p>

          {links.length ? (
            <m.nav {...itemProps} aria-label="Legal and help" className="mt-10 flex flex-wrap gap-x-5 gap-y-2 text-xs">
              {links.map((link) => (
                <FooterLink key={link.label} href={link.href}>
                  {link.label}
                </FooterLink>
              ))}
            </m.nav>
          ) : null}

          <p className="mt-10 text-xs text-fg-subtle md:hidden">
            © {year} {brand.companyName}
          </p>
        </m.div>
      </main>
    </div>
  );
}
