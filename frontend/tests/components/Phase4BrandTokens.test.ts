import { describe, expect, it } from "vitest";
import fs from "fs";
import path from "path";

describe("Phase 4: Brand Tokens & White-Labelling CSS Variables", () => {
  it("defines the Exigo Tech brand sweep gradient and glow CSS variables in globals.css", () => {
    const cssPath = path.resolve(__dirname, "../../app/globals.css");
    const cssContent = fs.readFileSync(cssPath, "utf-8");

    expect(cssContent).toContain("--brand-gradient-from: #4c48e3;");
    expect(cssContent).toContain("--brand-gradient-via: #6745d7;");
    expect(cssContent).toContain("--brand-gradient-to: #8242cc;");
    expect(cssContent).toContain("--brand-glow: rgba(76, 72, 227, 0.35);");
  });

  it("extends Tailwind config with brand-gradient and brand-glow utilities", () => {
    const configPath = path.resolve(__dirname, "../../tailwind.config.ts");
    const configContent = fs.readFileSync(configPath, "utf-8");

    expect(configContent).toContain("brand-gradient");
    expect(configContent).toContain("brand-glow");
    expect(configContent).toContain("var(--brand-gradient-from)");
  });
});
