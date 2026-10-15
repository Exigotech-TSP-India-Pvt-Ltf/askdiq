#!/usr/bin/env node
// npm audit gate (dependency-free).
//
// Runs `npm audit --json --omit=dev` and fails when:
//   - a high or critical advisory is NOT in the reviewed allow-list, or
//   - an allow-list entry is malformed or past its `expires` date.
// Allow-list: ../.github/audit/npm-audit-allowlist.json (override with
// AUDIT_ALLOWLIST=<path>). Entries match on GHSA id or advisory URL.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BLOCKING = new Set(["high", "critical"]);
const here = path.dirname(fileURLToPath(import.meta.url));
const allowlistPath =
  process.env.AUDIT_ALLOWLIST ??
  path.resolve(here, "..", "..", ".github", "audit", "npm-audit-allowlist.json");

function fail(message) {
  console.error(`audit-gate: ${message}`);
  process.exitCode = 1;
}

function loadAllowlist() {
  const raw = JSON.parse(readFileSync(allowlistPath, "utf8"));
  const entries = Array.isArray(raw) ? raw : raw.advisories ?? [];
  const today = new Date().toISOString().slice(0, 10);
  const keys = new Map();
  for (const entry of entries) {
    const key = entry.id ?? entry.url;
    if (!key || !entry.package || !entry.reason || !/^\d{4}-\d{2}-\d{2}$/.test(entry.expires ?? "")) {
      fail(`malformed allow-list entry (needs id/url, package, reason, expires YYYY-MM-DD): ${JSON.stringify(entry)}`);
      continue;
    }
    if (entry.expires < today) {
      fail(`allow-list entry ${key} (${entry.package}) expired on ${entry.expires}`);
      continue;
    }
    keys.set(key, entry);
    if (entry.url) keys.set(entry.url, entry);
  }
  return keys;
}

function runAudit() {
  // Fixed command string (no user input); a shell resolves npm/npm.cmd on all OSes.
  const result = spawnSync("npm audit --json --omit=dev", {
    encoding: "utf8",
    shell: true,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (!result.stdout) {
    throw new Error(`npm audit produced no output: ${result.stderr || result.error}`);
  }
  return JSON.parse(result.stdout);
}

function blockingAdvisories(report) {
  const found = new Map();
  for (const vuln of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vuln.via ?? []) {
      if (typeof via !== "object" || !BLOCKING.has(via.severity)) continue;
      const id = String(via.url ?? via.source).split("/").pop();
      found.set(id, { id, url: via.url, package: via.name, severity: via.severity, title: via.title });
    }
  }
  return [...found.values()];
}

const allowed = loadAllowlist();
const advisories = blockingAdvisories(runAudit());
const seen = new Set();
for (const adv of advisories) {
  const entry = allowed.get(adv.id) ?? allowed.get(adv.url);
  if (entry) {
    seen.add(entry);
    console.log(`allowed  ${adv.severity.padEnd(8)} ${adv.id} ${adv.package} (expires ${entry.expires})`);
  } else {
    fail(`${adv.severity} ${adv.id} in ${adv.package} is not allow-listed: ${adv.title} ${adv.url}`);
  }
}
for (const entry of new Set(allowed.values())) {
  if (!seen.has(entry)) {
    console.warn(`audit-gate: stale allow-list entry ${entry.id ?? entry.url} no longer reported; remove it`);
  }
}
console.log(
  `audit-gate: ${advisories.length} high/critical advisories, ${seen.size} allow-listed` +
    (process.exitCode ? " -> FAIL" : " -> OK"),
);
