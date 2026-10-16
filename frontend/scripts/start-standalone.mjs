// Start the production (standalone) build on PORT (default 3000).
// `next build` with output: "standalone" leaves static assets, public/ and
// .env.local out of .next/standalone; copy them in, then run its server.js.
import { cpSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";

const root = process.cwd();
const out = path.join(root, ".next", "standalone");
if (!existsSync(path.join(out, "server.js"))) {
  console.error("No standalone build found. Run `npm run build` first.");
  process.exit(1);
}
cpSync(path.join(root, ".next", "static"), path.join(out, ".next", "static"), { recursive: true });
if (existsSync(path.join(root, "public"))) {
  cpSync(path.join(root, "public"), path.join(out, "public"), { recursive: true });
}
for (const file of [".env", ".env.local", ".env.production", ".env.production.local"]) {
  if (existsSync(path.join(root, file))) cpSync(path.join(root, file), path.join(out, file));
}
const child = spawn(process.execPath, ["server.js"], {
  cwd: out,
  stdio: "inherit",
  env: { ...process.env, PORT: process.env.PORT ?? "3000", HOSTNAME: process.env.HOSTNAME ?? "0.0.0.0" },
});
child.on("exit", (code) => process.exit(code ?? 0));
