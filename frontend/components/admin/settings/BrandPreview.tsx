"use client";

import React from "react";
import { MessageSquare, LayoutDashboard, FileText, Sparkles } from "lucide-react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { BrandScope } from "@/lib/brand-context";
import type { Brand } from "@/lib/brand";
import {
  BRAND_SHADES,
  brandRamp,
  hexToRgbTriplet,
  isHexColor,
  microsoftLogoColors,
  semanticTokens,
  type SemanticTokens,
  type ThemeName,
} from "@/lib/theme-tokens";

/**
 * CSS variables for one preview pane: the theme's semantic tokens plus the
 * draft brand ramp, so token classes inside the pane render that theme with
 * the unsaved colours regardless of the page theme (side-by-side light/dark).
 */
export function previewVariables(theme: ThemeName, palette: Brand["palette"]): React.CSSProperties {
  const vars: Record<string, string> = {};
  for (const [name, hex] of Object.entries(semanticTokens[theme]) as [keyof SemanticTokens, string][]) {
    vars[`--${name}`] = hexToRgbTriplet(hex);
  }
  for (const shade of BRAND_SHADES) {
    const value = palette[shade];
    vars[`--brand-${shade}`] = hexToRgbTriplet(value && isHexColor(value) ? value : brandRamp[shade]);
  }
  vars.colorScheme = theme;
  return vars as React.CSSProperties;
}

function MicrosoftMark() {
  return (
    <span aria-hidden="true" className="grid h-3.5 w-3.5 grid-cols-2 gap-px">
      {Object.values(microsoftLogoColors).map((color) => (
        <span key={color} style={{ backgroundColor: color }} />
      ))}
    </span>
  );
}

function SidebarHeaderPreview({ theme, brand }: { theme: ThemeName; brand: Brand }) {
  const activeRow = theme === "dark" ? "bg-brand-950/60 text-brand-300" : "bg-brand-50 text-brand-700";
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface-card" data-testid={`preview-sidebar-${theme}`}>
      <div className="flex h-12 items-center border-b border-line px-3">
        <BrandLogo forceTheme={theme} className="h-7 max-w-[150px]" alt={`${brand.companyName} logo (${theme})`} />
      </div>
      <ul className="space-y-0.5 p-2 text-[11px] font-medium text-fg-muted">
        <li className={`relative flex items-center gap-2 rounded-md px-2 py-1 font-semibold ${activeRow}`}>
          <span className="absolute left-0 top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-r-full bg-brand-600" />
          <LayoutDashboard className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" /> Dashboard
        </li>
        <li className="flex items-center gap-2 px-2 py-1">
          <MessageSquare className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" /> Chat
        </li>
        <li className="flex items-center gap-2 px-2 py-1">
          <FileText className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" /> Documents
        </li>
      </ul>
    </div>
  );
}

function LoginCardPreview({ theme, brand }: { theme: ThemeName; brand: Brand }) {
  const link = theme === "dark" ? "text-brand-400" : "text-brand-600";
  const year = new Date().getFullYear();
  const links = (
    [
      ["Privacy Policy", brand.links.privacy],
      ["Terms of Service", brand.links.terms],
      ["Help", brand.links.help],
    ] as const
  ).filter(([, href]) => href);
  const contact = brand.accessContactEmail ?? brand.supportEmail;
  return (
    <div className="grid overflow-hidden rounded-xl border border-line sm:grid-cols-[53fr_47fr]" data-testid={`preview-login-${theme}`}>
      <div className="relative flex min-h-[180px] flex-col justify-between gap-3 overflow-hidden bg-brand-panel p-3 text-white">
        <span aria-hidden="true" className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full border border-white/10" />
        <span aria-hidden="true" className="pointer-events-none absolute -right-4 -top-4 h-20 w-20 rounded-full border border-white/10" />
        <BrandLogo forceTheme="dark" variant="mark" className="h-6 w-6" alt={`${brand.companyName} mark`} />
        <div className="relative space-y-1">
          {brand.login.eyebrow ? (
            <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-white/75">{brand.login.eyebrow}</p>
          ) : null}
          <p className="text-sm font-bold leading-tight">{brand.login.headline ?? `Welcome to ${brand.productName}`}</p>
          {brand.login.subtext ? <p className="line-clamp-3 text-[10px] text-white/80">{brand.login.subtext}</p> : null}
        </div>
        <p className="relative text-[9px] text-white/70">
          © {year} {brand.companyName}
        </p>
      </div>
      <div className="flex flex-col justify-center gap-2 bg-surface-page p-3">
        <div className="space-y-2 rounded-lg border border-line bg-surface-card p-3 shadow-xs">
          <p className="text-xs font-bold text-fg">Welcome to {brand.productName}</p>
          {brand.login.description ? <p className="line-clamp-3 text-[10px] text-fg-muted">{brand.login.description}</p> : null}
          <span className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-line-strong bg-surface-card px-2 py-1.5 text-[10px] font-semibold text-fg">
            <MicrosoftMark /> Sign in with Microsoft
          </span>
          <p className="flex items-center gap-1.5 text-[8px] font-semibold uppercase tracking-wider text-fg-subtle">
            <span className="h-px flex-1 bg-line" /> Single sign-on <span className="h-px flex-1 bg-line" />
          </p>
          <p className="text-[9px] text-fg-muted">
            Access is provisioned by your {brand.companyName} administrator. If you can&apos;t sign in, contact{" "}
            <span className={`font-semibold ${link}`}>{contact}</span>
          </p>
        </div>
        {links.length ? (
          <p className="flex flex-wrap justify-center gap-2 text-[9px] text-fg-subtle">
            {links.map(([label]) => (
              <span key={label}>{label}</span>
            ))}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function ChatBubblePreview({ theme, brand }: { theme: ThemeName; brand: Brand }) {
  const question = brand.chatSampleQuestions[0] ?? `What can ${brand.assistantName} help with?`;
  return (
    <div className="space-y-2 rounded-xl border border-line bg-surface-page p-3" data-testid={`preview-chat-${theme}`}>
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-3 py-1.5 text-[11px] text-white shadow-xs">{question}</p>
      </div>
      <div className="flex items-start gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-700">
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <div className="max-w-[85%] rounded-2xl rounded-tl-md border border-line bg-surface-card px-3 py-1.5 shadow-xs">
          <p className="text-[10px] font-semibold text-fg">{brand.assistantName}</p>
          <p className="text-[11px] text-fg-muted">
            Here is a grounded answer from your knowledge base, with{" "}
            <span className={`font-semibold underline ${theme === "dark" ? "text-brand-400" : "text-brand-600"}`}>sources</span>.
          </p>
        </div>
      </div>
    </div>
  );
}

function PreviewPane({ theme, brand }: { theme: ThemeName; brand: Brand }) {
  return (
    <figure
      aria-label={`${theme === "dark" ? "Dark" : "Light"} theme preview`}
      data-testid={`brand-preview-${theme}`}
      // `dark` scopes Tailwind dark: variants to the dark pane; the inline
      // variables pin each pane to its theme whatever the page theme is.
      className={`${theme === "dark" ? "dark" : ""} min-w-0 space-y-2 rounded-2xl border border-line bg-surface-page p-3 text-fg`}
      style={previewVariables(theme, brand.palette)}
    >
      <figcaption className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
        {theme === "dark" ? "Dark" : "Light"}
      </figcaption>
      <SidebarHeaderPreview theme={theme} brand={brand} />
      <LoginCardPreview theme={theme} brand={brand} />
      <ChatBubblePreview theme={theme} brand={brand} />
    </figure>
  );
}

/** Live preview of the unsaved brand: sidebar header, login card and chat bubble, light and dark side by side. */
export function BrandPreview({ brand }: { brand: Brand }) {
  return (
    <BrandScope brand={brand}>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2" data-testid="brand-preview">
        <PreviewPane theme="light" brand={brand} />
        <PreviewPane theme="dark" brand={brand} />
      </div>
    </BrandScope>
  );
}
