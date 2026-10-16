import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import fs from "fs";
import path from "path";
import { render, screen } from "@testing-library/react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { BrandProvider } from "@/lib/brand-context";
import { normalizeBrand } from "@/lib/brand";
import { brandRamp, contrastRatio, legacyDarkPalette, semanticTokens } from "@/lib/theme-tokens";
import { Sidebar } from "@/components/Sidebar";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "t", user: { full_name: "Jane Doe", email: "jane@example.com" }, logout: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin" }));

const PUBLIC = path.resolve(__dirname, "../../public");
const read = (file: string) => fs.readFileSync(path.join(PUBLIC, file), "utf-8");

function viewBoxAspect(svg: string): number {
  const match = svg.match(/viewBox="([^"]+)"/);
  expect(match, "viewBox").not.toBeNull();
  const [, , w, h] = match![1].trim().split(/[\s,]+/).map(Number);
  return w / h;
}

function pngAspect(file: string): number {
  const buf = fs.readFileSync(path.join(PUBLIC, file));
  return buf.readUInt32BE(16) / buf.readUInt32BE(20); // IHDR width / height
}

/** Every solid colour an SVG paints with: fill/stop-color attributes and CSS `fill:` rules. */
function paintColours(svg: string): string[] {
  const colours = new Set<string>();
  for (const m of svg.matchAll(/(?:fill|stop-color)\s*[:=]\s*"?\s*(#[0-9a-fA-F]{3,6})\b/g)) {
    colours.add(m[1].toUpperCase());
  }
  return [...colours];
}

describe("logo assets (F0.3.4, newplan §1.7)", () => {
  it("re-cropped full logo viewBox is within 5% of the tight PNG aspect (~4.06)", () => {
    const target = pngAspect("exigotech-logo.png");
    expect(target).toBeCloseTo(4.06, 1);
    for (const file of ["exigotech-logo.svg", "exigotech-logo-dark.svg"]) {
      const aspect = viewBoxAspect(read(file));
      expect(Math.abs(aspect - target) / target, `${file} aspect ${aspect}`).toBeLessThanOrEqual(0.05);
    }
  });

  it("fills at least 160 px of width at the h-10 (40 px) sidebar height", () => {
    expect(40 * viewBoxAspect(read("exigotech-logo.svg"))).toBeGreaterThanOrEqual(160);
  });

  it("dark variants use a white wordmark and the lifted brand-400 teal", () => {
    const colours = paintColours(read("exigotech-logo-dark.svg"));
    expect(colours).toContain("#FFFFFF");
    expect(colours).toContain(brandRamp["400"].toUpperCase());
    expect(paintColours(read("exigotech-mark-dark.svg"))).toContain(brandRamp["400"].toUpperCase());
  });

  it("every colour in the dark variants has >= 3:1 contrast against the dark background", () => {
    const darkBackgrounds = [semanticTokens.dark["surface-page"], semanticTokens.dark["surface-card"], legacyDarkPalette.surface];
    for (const file of ["exigotech-logo-dark.svg", "exigotech-mark-dark.svg"]) {
      const colours = paintColours(read(file));
      expect(colours.length, file).toBeGreaterThan(0);
      for (const colour of colours) {
        for (const bg of darkBackgrounds) {
          expect(contrastRatio(colour, bg), `${file} ${colour} on ${bg}`).toBeGreaterThanOrEqual(3);
        }
      }
    }
  });
});

describe("<BrandLogo />", () => {
  it("renders the full logo as a light/dark pair with the sidebar size classes", () => {
    render(<BrandLogo />);
    const [light, dark] = screen.getAllByRole("img", { hidden: true });
    expect(light).toHaveAttribute("src", "/exigotech-logo.svg");
    expect(light).toHaveClass("h-10", "w-auto", "max-w-[200px]", "dark:hidden");
    expect(light).not.toHaveClass("hidden");
    expect(dark).toHaveAttribute("src", "/exigotech-logo-dark.svg");
    expect(dark).toHaveClass("h-10", "w-auto", "max-w-[200px]", "hidden", "dark:block");
    expect(light).toHaveAttribute("alt", "Exigo Tech");
  });

  it("renders the mark variant with the mark sources", () => {
    render(<BrandLogo variant="mark" />);
    const [light, dark] = screen.getAllByRole("img", { hidden: true });
    expect(light).toHaveAttribute("src", "/exigotech-mark.svg");
    expect(dark).toHaveAttribute("src", "/exigotech-mark-dark.svg");
    expect(light).toHaveClass("h-7", "w-7");
  });

  it("uses the tenant's logos and company name from BrandProvider", () => {
    const brand = normalizeBrand({
      company_name: "Contoso",
      logo_light_url: "/branding/assets/light",
      logo_dark_url: "/branding/assets/dark",
    });
    render(
      <BrandProvider brand={brand}>
        <BrandLogo />
      </BrandProvider>,
    );
    const [light, dark] = screen.getAllByAltText("Contoso");
    expect(light).toHaveAttribute("src", "/branding/assets/light");
    expect(dark).toHaveAttribute("src", "/branding/assets/dark");
  });

  it("is what the Sidebar renders, inside an h-16 header", () => {
    const { container } = render(<Sidebar />);
    const sources = [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(sources).toEqual(["/exigotech-logo.svg", "/exigotech-logo-dark.svg"]);
    const header = container.querySelector("aside > div");
    expect(header).toHaveClass("h-16");
  });
});
