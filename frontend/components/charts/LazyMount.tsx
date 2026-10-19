"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Mounts `children` once the placeholder comes near the viewport (or right
 * away where IntersectionObserver is missing). Heavy below-the-fold charts
 * (thousands of SVG marks) then never block the first paint of a page.
 */
export function LazyMount({
  children,
  minHeight,
  rootMargin = "200px",
}: {
  children: ReactNode;
  minHeight: number;
  rootMargin?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (visible || !ref.current || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [rootMargin, visible]);

  if (visible) return <>{children}</>;
  return <div ref={ref} style={{ minHeight }} aria-hidden="true" />;
}
