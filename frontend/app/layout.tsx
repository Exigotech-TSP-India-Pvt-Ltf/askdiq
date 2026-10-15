import type { Metadata } from "next";
import { cache } from "react";
import { headers } from "next/headers";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import { BrandProvider } from "@/lib/brand-context";
import { brandCssText, buildMetadata, type Brand } from "@/lib/brand";
import { fetchBrand } from "@/lib/brand-server";
import { THEME_INIT_SCRIPT } from "@/lib/theme-script";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import "./globals.css";

// Branding is cached and re-fetched at most every 5 minutes (ISR).
export const revalidate = 300; // must be a literal; keep equal to BRANDING_REVALIDATE_SECONDS

const getBrand = cache((): Promise<Brand> => fetchBrand());

export async function generateMetadata(): Promise<Metadata> {
  return buildMetadata(await getBrand());
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const brand = await getBrand();
  const brandCss = brandCssText(brand);
  // Per-request CSP nonce from middleware.ts (reading headers() renders the
  // shell per request, which a nonce needs; /branding stays in the data cache).
  const nonce = headers().get("x-nonce") ?? undefined;

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
                <AuthProvider>{children}</AuthProvider>
              </ToastProvider>
            </MotionProvider>
          </BrandProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
