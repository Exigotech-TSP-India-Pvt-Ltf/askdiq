import type { Metadata } from "next";
import { cache } from "react";
import { cookies, headers } from "next/headers";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import { BrandProvider } from "@/lib/brand-context";
import { brandCssText, buildMetadata, type Brand } from "@/lib/brand";
import { fetchBrand } from "@/lib/brand-server";
import { sessionCookieName } from "@/lib/server/auth-cookies";
import { preloadMe } from "@/lib/server/session-me";
import { THEME_INIT_SCRIPT } from "@/lib/theme-script";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import "./globals.css";

// Branding is cached in-process by fetchBrand (re-fetched at most every
// BRANDING_REVALIDATE_SECONDS, each call bounded), not in Next's data cache.
const getBrand = cache((): Promise<Brand> => fetchBrand());

export async function generateMetadata(): Promise<Metadata> {
  return buildMetadata(await getBrand());
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Per-request CSP nonce from middleware.ts (reading headers() renders the
  // shell per request, which a nonce needs; /branding stays in the data cache).
  const requestHeaders = headers();
  const nonce = requestHeaders.get("x-nonce") ?? undefined;
  // Branding and the signed-in user load in parallel; with the user known on
  // the server the first HTML already holds the shell and page skeletons
  // (no spinner, no client /auth/me round trip before the page's own data).
  const [brand, initialMe] = await Promise.all([
    getBrand(),
    preloadMe({
      session: cookies().get(sessionCookieName())?.value,
      forwardedFor: requestHeaders.get("x-forwarded-for"),
      userAgent: requestHeaders.get("user-agent"),
    }),
  ]);
  const brandCss = brandCssText(brand);

  return (
    // suppressHydrationWarning: the pre-paint script below changes the <html>
    // class and color-scheme before React hydrates (T3).
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* T3: apply the stored / OS theme before first paint (no flash). Carries
            the middleware's CSP nonce; browsers hide nonce attributes after
            parsing, hence suppressHydrationWarning. */}
        <script
          id="theme-init"
          nonce={nonce}
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
        {brandCss ? <style id="brand-palette" dangerouslySetInnerHTML={{ __html: brandCss }} /> : null}
      </head>
      <body>
        <ThemeProvider>
          <BrandProvider brand={brand}>
            <MotionProvider>
              <ToastProvider>
                <AuthProvider initialMe={initialMe}>{children}</AuthProvider>
              </ToastProvider>
            </MotionProvider>
          </BrandProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
