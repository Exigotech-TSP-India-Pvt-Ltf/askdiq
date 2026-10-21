/**
 * VA-54: the audience scope starts from one server-resolved value, so the
 * server HTML and the first client render agree (no React #418 / #422).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { AudienceProvider, AudienceSeedProvider, useAudience } from "@/lib/audience-context";
import {
  AUDIENCE_COOKIE,
  audienceCookie,
  audienceFromCookieString,
  resolveInitialAudience,
} from "@/lib/audience-seed";
import type { Audience } from "@/lib/types";

function Probe() {
  const { audience } = useAudience();
  return <span data-testid="audience">{audience}</span>;
}

function Tree({ seed }: { seed: Audience }) {
  return (
    <AudienceSeedProvider audience={seed}>
      <AudienceProvider>
        <Probe />
      </AudienceProvider>
    </AudienceSeedProvider>
  );
}

function clearCookie() {
  document.cookie = `${AUDIENCE_COOKIE}=; Path=/; Max-Age=0`;
}

afterEach(() => {
  localStorage.clear();
  clearCookie();
  window.history.replaceState(null, "", "/");
  document.body.innerHTML = "";
});

describe("resolveInitialAudience", () => {
  it("prefers the URL, then the cookie, and ignores unknown values", () => {
    expect(resolveInitialAudience("testing", "users")).toBe("testing");
    expect(resolveInitialAudience(null, "users")).toBe("users");
    expect(resolveInitialAudience("root", "<script>")).toBe("overview");
    expect(resolveInitialAudience(undefined, undefined)).toBe("overview");
  });

  it("writes and reads a validated cookie", () => {
    expect(audienceCookie("users", true)).toBe(`${AUDIENCE_COOKIE}=users; Path=/; Max-Age=31536000; SameSite=Lax; Secure`);
    expect(audienceFromCookieString(`a=1; ${AUDIENCE_COOKIE}=testing`)).toBe("testing");
    expect(audienceFromCookieString(`${AUDIENCE_COOKIE}=evil`)).toBeNull();
  });
});

describe("AudienceProvider hydration", () => {
  it("hydrates without a mismatch although localStorage and the URL disagree with the server default", async () => {
    // The server rendered with the cookie value ("users"); the browser also
    // holds an older localStorage choice and the URL has no audience.
    const html = renderToString(<Tree seed="users" />);
    expect(html).toContain(">users<");
    localStorage.setItem("askdiq.audience_scope", "testing");
    document.cookie = `${AUDIENCE_COOKIE}=users; Path=/`;

    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const recoverable = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await act(async () => {
      hydrateRoot(container, <Tree seed="users" />, { onRecoverableError: recoverable });
    });
    expect(recoverable).not.toHaveBeenCalled();
    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/hydrat|did not match/i);
    expect(container.textContent).toBe("users");
    // The cookie wins over the stale localStorage value, and both are kept in step.
    expect(localStorage.getItem("askdiq.audience_scope")).toBe("users");
    consoleError.mockRestore();
  });

  it("moves a localStorage-only choice to the cookie after mount (no first-render read)", async () => {
    localStorage.setItem("askdiq.audience_scope", "testing");
    const html = renderToString(<Tree seed="overview" />);
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const recoverable = vi.fn();
    await act(async () => {
      hydrateRoot(container, <Tree seed="overview" />, { onRecoverableError: recoverable });
    });
    expect(recoverable).not.toHaveBeenCalled();
    expect(container.textContent).toBe("testing");
    expect(audienceFromCookieString(document.cookie)).toBe("testing");
  });
});
