# Website content

Verbatim copy of every page listed in [`public/sitemap.xml`](../public/sitemap.xml), one Markdown file per URL, extracted from the frontend source so the copy can be reviewed and edited without reading code.

These files are a **snapshot**, not the source of truth — the site still renders from the React components listed in each file's `source:` frontmatter. Copy edits made here must be applied back to those files.

## Conventions

- Frontmatter: `url`, `route`, `title` / `description` (page `<meta>`), `source` (files the copy came from).
- `## [Section: …]` — bracketed headings are labels for un-headed sections, not on-page copy.
- `_(…)_` — italic notes describe conditional states (signed in, tabs, steps), not on-page copy.
- `**[CTA]** Label → /href` — buttons and links.
- Site-wide Navbar, Footer and modals are in [`_global.md`](./_global.md), not repeated per page.

## Pages

| URL | File |
| --- | --- |
| / | [home.md](./home.md) |
| /platform | [platform.md](./platform.md) |
| /services | [services.md](./services.md) |
| /zero-trust | [zero-trust.md](./zero-trust.md) |
| /essential-eight | [essential-eight.md](./essential-eight.md) |
| /microsoft-365-security-baseline-assessment | [microsoft-365-security-baseline-assessment.md](./microsoft-365-security-baseline-assessment.md) |
| /iso-27001-microsoft-365-technical-controls | [iso-27001-microsoft-365-technical-controls.md](./iso-27001-microsoft-365-technical-controls.md) |
| /cis-microsoft-365-foundations-benchmark-assessment | [cis-microsoft-365-foundations-benchmark-assessment.md](./cis-microsoft-365-foundations-benchmark-assessment.md) |
| /iso-42001-ai-management-system | [iso-42001-ai-management-system.md](./iso-42001-ai-management-system.md) |
| /packages | [packages.md](./packages.md) |
| /responsible-ai | [responsible-ai.md](./responsible-ai.md) |
| /secure-ai | [secure-ai.md](./secure-ai.md) |
| /about-us | [about-us.md](./about-us.md) |
| /our-vision | [our-vision.md](./our-vision.md) |
| /contact-us | [contact-us.md](./contact-us.md) |
| /get-started | [get-started.md](./get-started.md) |
| /booking | [booking.md](./booking.md) |
| /privacy | [privacy.md](./privacy.md) |
| /terms | [terms.md](./terms.md) |
