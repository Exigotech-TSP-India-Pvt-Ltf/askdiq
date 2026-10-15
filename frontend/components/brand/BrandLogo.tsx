"use client";

/* eslint-disable @next/next/no-img-element -- SVG logos: next/image adds nothing and needs fixed dimensions. */
import { useBrand } from "@/lib/brand-context";

export type BrandLogoVariant = "full" | "mark";

const SIZE: Record<BrandLogoVariant, string> = {
  full: "h-10 w-auto max-w-[200px] object-contain object-left",
  mark: "h-7 w-7 object-contain",
};

interface BrandLogoProps {
  variant?: BrandLogoVariant;
  /**
   * "auto" swaps light / dark artwork with the theme; "onDark" always shows the
   * white (dark-theme) artwork, for brand-coloured panels such as the login.
   */
  tone?: "auto" | "onDark";
  className?: string;
  /** Overrides the accessible name (defaults to the brand's company name). */
  alt?: string;
}

/**
 * The single logo component (newplan §1.7 to §1.10, §14). Renders the light and
 * dark artwork as a `dark:hidden` / `hidden dark:block` pair, so the swap is
 * pure CSS and follows the theme instantly with no flash or re-render.
 */
export function BrandLogo({ variant = "full", tone = "auto", className = "", alt }: BrandLogoProps) {
  const brand = useBrand();
  const label = alt ?? brand.companyName;
  const [lightSrc, darkSrc] =
    variant === "full" ? [brand.logos.light, brand.logos.dark] : [brand.logos.markLight, brand.logos.markDark];
  const size = `${SIZE[variant]} ${className}`.trim();

  if (tone === "onDark") {
    return <img src={darkSrc} alt={label} data-theme-variant="dark" className={size} />;
  }

  return (
    <>
      <img src={lightSrc} alt={label} data-theme-variant="light" className={`${size} block dark:hidden`} />
      <img src={darkSrc} alt={label} data-theme-variant="dark" className={`${size} hidden dark:block`} />
    </>
  );
}
