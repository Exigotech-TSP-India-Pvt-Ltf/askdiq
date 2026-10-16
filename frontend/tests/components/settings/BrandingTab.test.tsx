import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BrandingTab } from "@/components/admin/settings/BrandingTab";
import { Providers, RAMP_3355CC, SHA_A, branding, file, installFetch, json, problem, resetSettingsState } from "./harness";

beforeEach(resetSettingsState);
afterEach(() => {
  vi.unstubAllGlobals();
  resetSettingsState();
});

function renderTab() {
  return render(
    <Providers>
      <BrandingTab />
    </Providers>,
  );
}

const hexInput = (label: string) => screen.getByRole("textbox", { name: label });
const saveButton = () => screen.getByRole("button", { name: /save changes/i });

async function loaded() {
  await screen.findByRole("form", { name: "Branding settings" });
}

describe("Settings → Branding", { timeout: 15_000 }, () => {
  it("loads the current branding into the form and shows the version", async () => {
    installFetch(({ method, url }) => (method === "GET" && url === "/api/proxy/branding" ? json(branding()) : undefined));
    renderTab();
    await loaded();
    expect(screen.getByLabelText("Product name")).toHaveValue("DeployIQ");
    expect(screen.getByLabelText("Eyebrow")).toHaveValue("ASKDIQ");
    expect(screen.getByLabelText("Privacy policy URL")).toHaveValue("/privacy");
    expect(screen.getByText("Version 3")).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("shows a live contrast badge that fails for an unreadable primary, and warns before saving", async () => {
    installFetch(({ method }) => (method === "GET" ? json(branding()) : undefined));
    renderTab();
    await loaded();
    const badges = () => screen.getAllByTestId("contrast-badge").slice(0, 2);
    expect(badges().map((b) => b.dataset.passes)).toEqual(["true", "true"]);

    fireEvent.change(hexInput("Primary"), { target: { value: "#777777" } });
    expect(badges()[0]).toHaveAttribute("data-passes", "false");
    expect(badges()[0]).toHaveTextContent("Fails AA 4.48:1");
    expect(screen.getByTestId("contrast-warning")).toHaveTextContent("brand-600 #777777 on white is 4.48:1 (needs 4.5:1)");
  });

  it("a 422 contrast rejection from the server shows the reason", async () => {
    const reason =
      "Primary colour #2a9d8f fails WCAG AA contrast: brand-600 #2a9d8f on white is 3.30:1 (needs 4.5:1); choose a darker primary colour";
    const { calls } = installFetch(({ method }) =>
      method === "GET" ? json(branding()) : method === "PUT" ? problem(422, reason) : undefined,
    );
    renderTab();
    await loaded();
    fireEvent.change(hexInput("Primary"), { target: { value: "#2A9D8F" } });
    fireEvent.click(saveButton());

    const banner = await screen.findByTestId("branding-server-error");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent(reason);
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/proxy/admin/branding");
    expect(put?.body).toEqual({ version: 3, primary_hex: "#2a9d8f" });
  });

  it("saving updates the --brand-* CSS variables app-wide without a reload and reports success", async () => {
    installFetch(({ method }) => {
      if (method === "GET") return json(branding());
      if (method === "PUT") return json(branding({ primary_hex: "#3355cc", palette: RAMP_3355CC, version: 4 }));
      return undefined;
    });
    renderTab();
    await loaded();
    fireEvent.change(hexInput("Primary"), { target: { value: "#3355cc" } });
    // Live preview follows the draft before saving; the app does not.
    expect(screen.getByTestId("brand-preview-light").style.getPropertyValue("--brand-600")).toBe("51 85 204");
    expect(document.documentElement.style.getPropertyValue("--brand-600")).toBe("");

    fireEvent.click(saveButton());
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--brand-600")).toBe("51 85 204"));
    expect(document.documentElement.style.getPropertyValue("--brand-50")).toBe("236 242 255");
    expect(await screen.findByText("Branding saved")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Version 4")).toBeInTheDocument());
  });

  it("a 409 version conflict shows the toast and reloads the latest branding", async () => {
    let gets = 0;
    installFetch(({ method }) => {
      if (method === "GET") {
        gets += 1;
        return json(gets === 1 ? branding() : branding({ product_name: "Contoso IQ", version: 5 }));
      }
      if (method === "PUT") return problem(409, "Branding was changed by someone else. Reload and try again.");
      return undefined;
    });
    renderTab();
    await loaded();
    fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Exigo" } });
    fireEvent.click(saveButton());

    expect(await screen.findByText("Branding was changed by someone else")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Product name")).toHaveValue("Contoso IQ"));
    expect(screen.getByLabelText("Company name")).toHaveValue("Exigo Tech");
    expect(screen.getByText("Version 5")).toBeInTheDocument();
  });

  it("validates link URLs client-side: https:// or relative only", async () => {
    const { calls } = installFetch(({ method }) => (method === "GET" ? json(branding()) : undefined));
    renderTab();
    await loaded();
    const privacy = screen.getByLabelText("Privacy policy URL");
    for (const bad of ["javascript:alert(1)", "http://example.com/privacy", "//evil.example/x", "data:text/html,x"]) {
      fireEvent.change(privacy, { target: { value: bad } });
      expect(privacy).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText("Must start with https:// or be a relative path such as /privacy.")).toBeInTheDocument();
    }
    fireEvent.click(saveButton());
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    fireEvent.change(privacy, { target: { value: "https://contoso.example/privacy" } });
    expect(privacy).toHaveAttribute("aria-invalid", "false");
    fireEvent.change(privacy, { target: { value: "/legal/privacy" } });
    expect(privacy).toHaveAttribute("aria-invalid", "false");
  });

  it("sends only changed fields, and an emptied optional field as null", async () => {
    const { calls } = installFetch(({ method, body }) =>
      method === "GET" ? json(branding()) : method === "PUT" ? json(branding({ ...(body as object), version: 4 })) : undefined,
    );
    renderTab();
    await loaded();
    fireEvent.change(screen.getByLabelText("Privacy policy URL"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Headline"), { target: { value: "  Secure answers  " } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      version: 3,
      privacy_url: null,
      login_headline: "Secure answers",
    });
  });

  it("edits the chat sample questions list (add, reorder, remove)", async () => {
    const { calls } = installFetch(({ method, body }) =>
      method === "GET" ? json(branding()) : method === "PUT" ? json(branding({ ...(body as object), version: 4 })) : undefined,
    );
    renderTab();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Add question" }));
    fireEvent.change(screen.getByLabelText("Sample question 3"), { target: { value: "Who do I call?" } });
    fireEvent.click(screen.getByRole("button", { name: "Move question 3 up" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove question 1" }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      version: 3,
      chat_sample_questions: ["Who do I call?", "How is data handled?"],
    });
  });

  it("logo upload pre-check rejects a bad type or an oversized file without uploading", async () => {
    const { calls } = installFetch(({ method }) => (method === "GET" ? json(branding()) : undefined));
    renderTab();
    await loaded();
    const zone = screen.getByTestId("dropzone-Logo (light)");

    fireEvent.drop(zone, { dataTransfer: { files: [file("logo.gif", "image/gif", 1000)] } });
    expect(await screen.findByText("logo.gif is not a PNG, WebP or SVG image.")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("file-Logo (light)"), { target: { files: [file("big.png", "image/png", 600 * 1024)] } });
    expect(await screen.findByText("big.png is 600 KB; the limit is 512 KB.")).toBeInTheDocument();

    fireEvent.drop(zone, { dataTransfer: { files: [file("fake.png", "image/svg+xml", 1000)] } });
    expect(await screen.findByText("fake.png is not a PNG, WebP or SVG image.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("uploads a valid logo by drag and drop, previews it and saves its asset id", async () => {
    const { calls } = installFetch(({ method, url, body }) => {
      if (method === "GET") return json(branding());
      if (method === "POST" && url === "/api/proxy/admin/branding/assets") {
        expect(body).toBeInstanceOf(FormData);
        return json(
          { id: SHA_A, mime: "image/png", size_bytes: 1000, width: 200, height: 40, url: `/branding/assets/${SHA_A}`, created_at: null },
          201,
        );
      }
      if (method === "PUT") return json(branding({ ...(body as object), version: 4 }));
      return undefined;
    });
    renderTab();
    await loaded();
    fireEvent.drop(screen.getByTestId("dropzone-Logo (light)"), { dataTransfer: { files: [file("logo.png", "image/png", 1000)] } });

    const preview = screen.getByTestId("preview-sidebar-light");
    await waitFor(() => expect(within(preview).getByRole("img")).toHaveAttribute("src", `/branding/assets/${SHA_A}`));
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ version: 3, logo_light_asset_id: SHA_A });
  });

  it("shows the server's upload rejection (413 / 415)", async () => {
    installFetch(({ method }) =>
      method === "GET" ? json(branding()) : method === "POST" ? problem(415, "Not a PNG, WebP or SVG image.") : undefined,
    );
    renderTab();
    await loaded();
    fireEvent.drop(screen.getByTestId("dropzone-Favicon"), { dataTransfer: { files: [file("icon.svg", "image/svg+xml", 1000)] } });
    expect(await screen.findByText("Not a PNG, WebP or SVG image.")).toBeInTheDocument();
  });

  it("Reset to defaults goes through ConfirmDialog and sends the seeded defaults", async () => {
    const { calls } = installFetch(({ method, body }) =>
      method === "GET"
        ? json(branding({ product_name: "Contoso IQ", primary_hex: "#3355cc", palette: RAMP_3355CC, logo_light_asset_id: SHA_A }))
        : method === "PUT"
          ? json(branding({ version: 4 }))
          : undefined,
    );
    renderTab();
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));
    const dialog = await screen.findByRole("alertdialog", { name: "Reset branding to defaults?" });
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Reset to defaults" }));
    });
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      version: 3,
      product_name: "DeployIQ",
      assistant_name: "AskDIQ",
      company_name: "Exigo Tech",
      primary_hex: "#008080",
      accent_hex: null,
      tint_hex: null,
      logo_light_asset_id: null,
      favicon_asset_id: null,
      privacy_url: null,
      login_headline: null,
    });
    expect(await screen.findByText("Branding reset to defaults")).toBeInTheDocument();
  });

  it("renders the live preview side by side in light and dark, pinned whatever the page theme", async () => {
    installFetch(({ method }) => (method === "GET" ? json(branding({ login_headline: "Hello Contoso" })) : undefined));
    renderTab();
    await loaded();
    const light = screen.getByTestId("brand-preview-light");
    const dark = screen.getByTestId("brand-preview-dark");
    expect(light).not.toHaveClass("dark");
    expect(dark).toHaveClass("dark");
    expect(light.style.getPropertyValue("--surface-card")).toBe("255 255 255");
    expect(dark.style.getPropertyValue("--surface-card")).toBe("11 17 19");

    // The page switching to dark leaves both panes pinned.
    document.documentElement.classList.add("dark");
    expect(light.style.getPropertyValue("--surface-card")).toBe("255 255 255");

    // Logos: the light pane shows the light artwork, the dark pane the dark artwork.
    expect(within(screen.getByTestId("preview-sidebar-light")).getByRole("img")).toHaveAttribute("src", "/exigotech-logo.svg");
    expect(within(screen.getByTestId("preview-sidebar-dark")).getByRole("img")).toHaveAttribute("src", "/exigotech-logo-dark.svg");
    // Login card and chat bubble render the draft copy in both panes.
    expect(within(screen.getByTestId("preview-login-light")).getByText("Hello Contoso")).toBeInTheDocument();
    expect(within(screen.getByTestId("preview-login-dark")).getByText("Hello Contoso")).toBeInTheDocument();
    expect(within(screen.getByTestId("preview-chat-dark")).getByText("What is the Essential Eight?")).toBeInTheDocument();
    // Footer links are hidden when empty.
    expect(within(screen.getByTestId("preview-login-light")).getByText("Privacy Policy")).toBeInTheDocument();
    expect(within(screen.getByTestId("preview-login-light")).queryByText("Terms of Service")).not.toBeInTheDocument();
  });

  it("shows an error state with retry when branding cannot load", async () => {
    let fail = true;
    installFetch(({ method }) => (method === "GET" ? (fail ? problem(500, "boom") : json(branding())) : undefined));
    renderTab();
    expect(await screen.findByText("Could not load branding")).toBeInTheDocument();
    expect(screen.getByText("req-123")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await loaded();
  });
});
