import Image from "next/image";

/**
 * Pre-optimised 256 px WebP (15 KB) of `public/brand/askdiq-mascot.png`
 * (the 1254 px source). Served as-is (`unoptimized`): it is already sized for
 * 3x displays at the largest use, and the standalone image server needs no
 * extra work per request.
 */
export const MASCOT_SRC = "/brand/askdiq-mascot-256.webp";

/** The AskDIQ mascot. Decorative by default (the surrounding control carries the name). */
export function AskDiqMascot({ size, alt = "", className = "", priority = false }: { size: number; alt?: string; className?: string; priority?: boolean }) {
  return (
    <Image
      src={MASCOT_SRC}
      alt={alt}
      width={size}
      height={size}
      unoptimized
      priority={priority}
      draggable={false}
      className={`select-none ${className}`}
    />
  );
}
