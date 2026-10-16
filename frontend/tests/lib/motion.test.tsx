import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import fs from "fs";
import path from "path";
import { render, screen, waitFor } from "@testing-library/react";
import { m } from "motion/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import {
  cardHover,
  countUp,
  crossfade,
  drawer,
  duration,
  durationMs,
  fadeUp,
  pop,
  reduceVariants,
  stagger,
  toast,
  useMotionVariants,
} from "@/lib/motion";

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lib/motion presets", () => {
  it("uses the 120 / 200 / 320 ms scale (seconds and ms agree)", () => {
    expect(durationMs).toMatchObject({ fast: 120, base: 200, slow: 320 });
    expect(duration.fast * 1000).toBeCloseTo(durationMs.fast);
    expect(duration.base * 1000).toBeCloseTo(durationMs.base);
    expect(duration.slow * 1000).toBeCloseTo(durationMs.slow);
    expect(duration.hover * 1000).toBeCloseTo(durationMs.hover);
  });

  it("exports every preset with its states", () => {
    for (const v of [fadeUp, drawer, toast, pop, crossfade]) {
      expect(v).toHaveProperty("hidden");
      expect(v).toHaveProperty("visible");
      expect(v).toHaveProperty("exit");
    }
    expect(stagger.visible).toHaveProperty("transition.staggerChildren");
    expect(cardHover).toMatchObject({ rest: { y: 0 }, hover: { y: -2 } });
    expect(countUp).toMatchObject({ duration: duration.slow });
  });

  it("keeps entrance and hover tweens under 300 ms", () => {
    const tween = (v: unknown) => (v as { transition?: { duration?: number } }).transition?.duration;
    for (const d of [tween(fadeUp.visible), tween(crossfade.visible), tween(cardHover.hover)]) {
      expect(d).toBeLessThan(0.3);
    }
  });

  it("reduceVariants strips every transform but keeps opacity", () => {
    for (const preset of [fadeUp, pop, drawer, toast, cardHover]) {
      for (const variant of Object.values(reduceVariants(preset))) {
        const keys = Object.keys(variant as object);
        for (const k of ["x", "y", "scale", "rotate"]) expect(keys).not.toContain(k);
      }
    }
    expect(reduceVariants(fadeUp).hidden).toEqual({ opacity: 0 });
    expect(reduceVariants(drawer).hidden).toEqual({});
  });
});

function Probe() {
  const variants = useMotionVariants(fadeUp);
  return <pre data-testid="variants">{JSON.stringify(variants.hidden)}</pre>;
}

describe("MotionProvider and reduced motion", () => {
  it("resolves the variants to no transform when the user prefers reduced motion", () => {
    mockReducedMotion(true);
    render(
      <MotionProvider>
        <Probe />
      </MotionProvider>,
    );
    expect(JSON.parse(screen.getByTestId("variants").textContent ?? "{}")).toEqual({ opacity: 0 });
  });

  it("keeps the transform when reduced motion is off", () => {
    mockReducedMotion(false);
    render(
      <MotionProvider>
        <Probe />
      </MotionProvider>,
    );
    expect(JSON.parse(screen.getByTestId("variants").textContent ?? "{}")).toEqual({ opacity: 0, y: 8 });
  });

  it("renders m.* inside LazyMotion and ends with no transform under reduced motion", async () => {
    mockReducedMotion(true);
    render(
      <MotionProvider>
        <m.div data-testid="card" variants={fadeUp} initial="hidden" animate="visible">
          hello
        </m.div>
      </MotionProvider>,
    );
    const card = screen.getByTestId("card");
    expect(card).toHaveTextContent("hello");
    await waitFor(() => {
      expect(["", "none"]).toContain(card.style.transform);
    });
  });
});

describe("motion usage guard", () => {
  const root = path.resolve(__dirname, "../..");
  const walk = (dir: string): string[] =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) return walk(p);
      return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
    });
  const files = ["app", "components", "lib"].flatMap((d) => walk(path.join(root, d)));
  const MOTION_IMPORT = /from\s+["']motion\/react["']/;

  it("never imports the full `motion` component or framer-motion", () => {
    for (const file of files) {
      const src = fs.readFileSync(file, "utf-8");
      expect(src, file).not.toMatch(/from\s+["']framer-motion["']/);
      expect(src, file).not.toMatch(/import\s*\{[^}]*\bmotion\b[^}]*\}\s*from\s*["']motion\/react["']/);
    }
  });

  it("has no hard-coded animation timings outside lib/motion.ts", () => {
    const motionFiles = files.filter(
      (f) => !f.endsWith(path.join("lib", "motion.ts")) && MOTION_IMPORT.test(fs.readFileSync(f, "utf-8")),
    );
    expect(motionFiles.length).toBeGreaterThan(0);
    for (const file of motionFiles) {
      const src = fs.readFileSync(file, "utf-8");
      expect(src, file).not.toMatch(/\b(duration|delay|staggerChildren|stiffness|damping)\s*:\s*[\d.]+/);
    }
  });
});
