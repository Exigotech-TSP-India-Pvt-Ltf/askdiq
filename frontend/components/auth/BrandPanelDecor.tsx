"use client";

import React from "react";
import { m, useSpring, useTransform, type MotionValue } from "motion/react";
import { ambientLoop, parallaxSpring, type ambientLoopSeconds } from "@/lib/motion";

type Loop = keyof typeof ambientLoopSeconds;

interface Shape {
  kind: "hex" | "ring";
  /** Position (CSS percentages) and size (px) inside the panel. */
  top: string;
  left: string;
  size: number;
  /** Pointer parallax depth in px at the panel edge. */
  depth: number;
  loop: Loop;
  drift: { x: number; y: number; rotate: number };
  className: string;
}

/** Thin hexagon outlines echoing the logo geometry, plus two faint concentric rings. */
const SHAPES: readonly Shape[] = [
  { kind: "ring", top: "52%", left: "48%", size: 620, depth: 6, loop: "long", drift: { x: 0, y: 0, rotate: 12 }, className: "text-white/10" },
  { kind: "ring", top: "62%", left: "60%", size: 400, depth: 10, loop: "medium", drift: { x: 0, y: 0, rotate: -10 }, className: "text-white/10" },
  { kind: "hex", top: "8%", left: "62%", size: 150, depth: 18, loop: "medium", drift: { x: 14, y: -10, rotate: 10 }, className: "text-white/25" },
  { kind: "hex", top: "30%", left: "80%", size: 90, depth: 26, loop: "short", drift: { x: -10, y: 12, rotate: -14 }, className: "text-white/20" },
  { kind: "hex", top: "64%", left: "6%", size: 120, depth: 14, loop: "long", drift: { x: 12, y: 8, rotate: 8 }, className: "text-white/15" },
  { kind: "hex", top: "78%", left: "70%", size: 200, depth: 10, loop: "medium", drift: { x: -12, y: -8, rotate: 6 }, className: "text-white/15" },
  { kind: "hex", top: "18%", left: "14%", size: 64, depth: 30, loop: "short", drift: { x: 8, y: 10, rotate: -18 }, className: "text-white/30" },
  { kind: "hex", top: "46%", left: "36%", size: 44, depth: 34, loop: "medium", drift: { x: -8, y: -12, rotate: 20 }, className: "text-white/20" },
];

function ShapeSvg({ kind, size }: Pick<Shape, "kind" | "size">) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden="true" focusable="false">
      {kind === "hex" ? (
        <polygon
          points="50,3 90.7,26.5 90.7,73.5 50,97 9.3,73.5 9.3,26.5"
          stroke="currentColor"
          strokeWidth={1.4}
          vectorEffect="non-scaling-stroke"
        />
      ) : (
        <>
          <circle cx="50" cy="50" r="49" stroke="currentColor" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <circle cx="50" cy="50" r="36" stroke="currentColor" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        </>
      )}
    </svg>
  );
}

function placement(shape: Shape): React.CSSProperties {
  return { top: shape.top, left: shape.left, width: shape.size, height: shape.size, marginLeft: -shape.size / 2, marginTop: -shape.size / 2 };
}

function AnimatedShape({ shape, pointerX, pointerY }: { shape: Shape; pointerX: MotionValue<number>; pointerY: MotionValue<number> }) {
  const x = useSpring(useTransform(pointerX, (v) => v * shape.depth), parallaxSpring);
  const y = useSpring(useTransform(pointerY, (v) => v * shape.depth), parallaxSpring);
  return (
    <m.div className={`absolute ${shape.className}`} style={{ ...placement(shape), x, y }}>
      <m.div
        animate={{ x: [0, shape.drift.x], y: [0, shape.drift.y], rotate: [0, shape.drift.rotate] }}
        transition={ambientLoop(shape.loop)}
      >
        <ShapeSvg kind={shape.kind} size={shape.size} />
      </m.div>
    </m.div>
  );
}

interface BrandPanelDecorProps {
  /** Pointer position over the panel, -0.5..0.5 on each axis. */
  pointerX: MotionValue<number>;
  pointerY: MotionValue<number>;
  /** Static decoration (prefers-reduced-motion). */
  reduced: boolean;
}

/**
 * Login brand-panel decoration: translucent hexagons and rings
 * that drift and rotate very slowly (20 to 40 s, transform only) and follow
 * the pointer with a subtle parallax. Completely static under reduced motion.
 */
export function BrandPanelDecor({ pointerX, pointerY, reduced }: BrandPanelDecorProps) {
  return (
    <div
      aria-hidden="true"
      data-testid="brand-panel-decor"
      data-motion={reduced ? "static" : "animated"}
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {SHAPES.map((shape, i) =>
        reduced ? (
          <div key={i} className={`absolute ${shape.className}`} style={placement(shape)}>
            <ShapeSvg kind={shape.kind} size={shape.size} />
          </div>
        ) : (
          <AnimatedShape key={i} shape={shape} pointerX={pointerX} pointerY={pointerY} />
        ),
      )}
    </div>
  );
}
