# Settings

> Superadmin page for white-label branding and the insights taxonomy.  Route: `/admin/settings` · Nav: **Settings** · Permission: `branding:write` (Branding tab), `taxonomy:write` (Insights Taxonomy writes) (superadmin only)

## Purpose and who uses it

Settings has two tabs:

- **Branding**: the white-label identity of the whole product. This covers product, assistant and company names, contact emails, brand colours (with a generated 50-950 palette and WCAG checks), logos and favicon, login-page copy, footer links and the chat sample questions. A save is live for every user, on the login page, the dashboard shell and Chat.
- **Insights Taxonomy**: the codes the conversation classifier assigns to every chat exchange: **Intents** (what the visitor asked about), **Bot quality** (how well the assistant answered) and **Issues** (why an answer fell short). These labels drive the Analytics and Conversation Logs insights.

Who uses it: superadmins only. In the permission table both `branding:write` and `taxonomy:write` belong to the superadmin role alone (`backend/app/auth/permissions.py`). Admins and operators do not see the nav item. If they open the URL they get the **Superadmin only** empty state.

## How to get there

- **Sidebar**: **Settings** (sliders icon). It is shown only when your role grants `branding:write`.
- **URL**: `/admin/settings` (Branding) or `/admin/settings?tab=taxonomy` (Insights Taxonomy).
- **Command palette** (Ctrl/Cmd+K): type "Settings", or the keywords `branding`, `taxonomy` or `white-label`.

Deep-link parameters:

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `branding`, `taxonomy` | `branding` | Selected tab (any other value falls back to Branding) |

The taxonomy sub-tab (Intents / Bot quality / Issues) and the **Show archived** toggle are not stored in the URL. They reset to **Intents** with archived entries hidden.

## Layout at a glance

```text
+----------------------------------------------------------------------------+
| [sliders] Settings                                                         |
| White-label branding and the insights taxonomy (superadmin)                |
+----------------------------------------------------------------------------+
| [Branding] [Insights Taxonomy]                                             |
+--------------------------------------+-------------------------------------+
| Identity     (names, emails)         | Live preview     Version N /        |
| Colours      Primary Accent Tint     |                  Unsaved changes    |
|   [contrast badges] [ramp 50..950]   | +---------------+ +---------------+ |
| Logos        5 drop zones            | | Light         | | Dark          | |
| Login page   eyebrow, headline, ...  | | sidebar head  | | sidebar head  | |
| Links        privacy, terms, help    | | login card    | | login card    | |
| Chat sample questions (1 to 8)       | | chat bubble   | | chat bubble   | |
|                                      | +---------------+ +---------------+ |
+--------------------------------------+-------------------------------------+
| [Reset to defaults]                         [Discard] [Save changes]       |
+----------------------------------------------------------------------------+

Insights Taxonomy tab:
+----------------------------------------------------------------------------+
| [Intents (13)] [Bot quality (9)] [Issues (10)]       [ ] Show archived (n) |
| Codes are permanent once created ...                                       |
| Order | Code    | Label            | Colour | Group      | Status | Actions|
| ^ v   | [l] SVC | [Platform and..] | [#..]  | [Customer] | Active | [arch] |
| Add intent:  Code [e.g. BILLING] Label [..] Colour [#RRGGBB] Group [..] Add|
+----------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/settings-branding.png`: Branding form with a custom primary colour, contrast badges, the derived ramp strip and the light/dark live preview.

> Screenshot placeholder: `img/settings-taxonomy.png`: Insights Taxonomy, Intents sub-tab, one row edited (Save button visible) and the Add intent form.

## Layout tour

### Header and access guard

- Title **Settings**, subtitle **White-label branding and the insights taxonomy (superadmin)**. No audience tabs and no date range. Neither the global Audience filter nor the date range applies here.
- Tabs: **Branding** (tooltip **White-label names, colours, logos and login copy**) and **Insights Taxonomy** (**Intents, bot-quality values and issues**).
- The page renders the tabs if you have `branding:write` **or** `taxonomy:write` (`useCan`). With neither, it shows **Superadmin only** / **Branding and the insights taxonomy can only be changed by a superadmin.** The backend repeats every check (see [API endpoints used](#api-endpoints-used)).

Source: `frontend/app/(app)/admin/settings/page.tsx`.

### Branding tab

The form loads `GET /branding` (the public endpoint, via the proxy, `cache: "no-store"`; no refetch on window focus). It keeps a baseline copy. **Save changes** sends only the fields you changed, plus the `version` you loaded (`PUT /admin/branding`). Validation runs live in the browser (`components/admin/settings/branding-form.ts`). Errors show once you edit a field, or for every field after you press Save. The server re-validates everything.

Source: `frontend/components/admin/settings/BrandingTab.tsx`, `branding-form.ts`, `fields.tsx`, `ColorField.tsx`, `AssetDropzone.tsx`, `QuestionsEditor.tsx`, `BrandPreview.tsx`; backend `backend/app/api/routes/branding.py`, `backend/app/branding/service.py`, `backend/app/schemas/branding.py`.

#### Identity

Card **Identity**, description **Names shown across the console, login page and chat.**

| Field | Required | Limit / rule | Notes |
|---|---|---|---|
| **Product name** | yes | 80 chars, single line | Login headline fallback ("Welcome to ...") and page metadata |
| **Assistant name** | yes | 80 chars, single line | Name shown on assistant replies in Chat |
| **Company name** | yes | 120 chars, single line | Login footer "© year Company", "Access is provisioned by your Company administrator" |
| **Support email** | yes | valid email | Fallback contact |
| **Access contact email** | no | valid email or empty | Hint: **Shown on the login and Access Required pages. Empty uses the support email.** |

Error texts: **Required.**, **At most N characters.**, **Use a single line.**, **Enter a valid email address.**, **Enter a valid email address, or leave it empty.**

The server resolves an empty access contact to the backend `ACCESS_CONTACT_EMAIL` setting when that is set. The live preview falls back to the support email.

#### Colours

Card **Colours**, description **Live WCAG check; the server re-checks on save.** Each colour has a native picker plus a `#RRGGBB` text box (error **Use a #RRGGBB colour.**).

- **Primary** (required). Hint **Derives the 50 to 950 ramp.** Two badges: **Light** and **Dark**.
- **Accent** (optional). It is empty by default, and the placeholder shows **Derived (#xxxxxx)** = ramp step 800. Badge **On white**. A reset icon ("Use the derived accent") clears it back to derived.
- **Tint** (optional). Derived = ramp step 50. Badge **Text on tint**.
- Below the fields, a strip shows the derived ramp (hover a swatch for `brand-<shade> #hex`).

How the palette is generated (`frontend/lib/brand-palette.ts::deriveRamp`, mirroring `backend/app/branding/palette.py::derive_ramp`):

- The primary colour becomes `brand-600` exactly.
- The other steps are computed in OKLCH and keep the hue. Lighter steps (50-500) move lightness towards 0.985 by fractions 0.95, 0.87, 0.72, 0.55, 0.38 and 0.19. Darker steps (700-950) move towards 0.18 by 0.2, 0.42, 0.62 and 0.8.
- Chroma is reduced towards the ends: up to 80% at the light end and 35% at the dark end. It is then clipped into sRGB with a 24-step binary search.

Contrast checks (WCAG 2.x ratio):

| Badge | Foreground / background | Threshold | Blocks save? |
|---|---|---|---|
| Primary **Light** | `brand-600` on the light card colour (white) | 4.5:1 | **Yes**: the server answers 422 |
| Primary **Dark** | `brand-400` on the dark page background (`#090d11` on the server) | 4.5:1 | **Yes**: the server answers 422 |
| Accent **On white** | accent (or derived 800) on the light card colour | 3:1 (UI components) | No, advisory only |
| Tint **Text on tint** | light-theme body text on tint (or derived 50) | 4.5:1 | No, advisory only |

Badges read **`<label> AA <ratio>`** or **`<label> Fails AA <ratio>`** (for example `Light AA 4.77:1`). If the primary fails, a warning below the fields repeats the server's wording and ends with **Saving will be rejected.** For example: "brand-600 #xxxxxx on white is 3.10:1 (needs 4.5:1); choose a darker primary colour". The server only checks the primary ramp (`check_palette`); accent and tint are never rejected for contrast.

#### Logos

Card **Logos**, description **Drag and drop, or choose a file. Dark artwork is shown on dark surfaces.** Five drop zones: **Logo (light)**, **Logo (dark)**, **Mark (light)**, **Mark (dark)**, **Favicon**. Dark slots preview on a dark tile. Each tile shows the current artwork, or the built-in default when no custom asset is set. A bin icon ("Use the default ...") appears when a custom asset is set.

Upload constraints:

- Client pre-check (`lib/settings-api.ts::checkAssetFile`): PNG, WebP or SVG only, and the file extension must match the type. The file must not be empty and must be at most 512 KB. Tile hint: **PNG, WebP or SVG, up to 512 KB.** Errors: "`<name>` is not a PNG, WebP or SVG image.", "`<name>` is empty.", "`<name>` is N KB; the limit is 512 KB."
- Server (`backend/app/branding/assets.py::validate_asset`):
  - The type is detected from the bytes, not the file name. A declared type that does not match the bytes gives 415.
  - Files over 512 KB give 413 (`BRANDING_ASSET_MAX_BYTES`).
  - PNG and WebP must have a size and be at most 4096 x 4096 px (`BRANDING_ASSET_MAX_DIMENSION`). Otherwise the server answers 422 "Image is WxH; the maximum is 4096x4096.". The UI does not mention this limit.
  - PNG chunks are CRC-checked, and trailing bytes are rejected.
  - SVG is parsed safely and rebuilt from an allow-list. Scripts, `foreignObject`, `style` elements, `on*` handlers, external `href` and `javascript:` / `data:` URLs are removed. The limits are 5,000 elements and 32 levels deep.
- Assets are content-addressed: the id is the sha256 of the (sanitised) bytes. Uploading the same file twice reuses the same asset.

The upload happens **immediately** when you drop or choose a file (`POST /admin/branding/assets`, audited as `branding_asset_uploaded`). The new image then appears in the form and the preview, but users only see it after **Save changes**. Uploaded files are never deleted, including when you clear a slot or reset.

Where assets are served: `GET /branding` returns relative URLs `/branding/assets/<sha256>`. The browser loads them from the Next route `frontend/app/branding/assets/[sha]/route.ts`. That route accepts only a 64-char lowercase hex id, forwards to the public backend route `GET /branding/assets/{sha}` without credentials, and passes back the caching and safety headers. Responses are `Cache-Control: public, max-age=31536000, immutable`, with an ETag equal to the sha (304 on match) and `X-Content-Type-Options: nosniff`. SVGs also get `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`.

#### Login page

Card **Login page**, description **Copy for the split-screen sign-in page.**

- **Eyebrow** (60 chars, single line). Hint **Small uppercase label, e.g. the product name.**
- **Headline** (160 chars, single line). If empty, the preview shows "Welcome to `<Product name>`".
- **Supporting copy** (400 chars, multi-line).
- **Card description** (1000 chars, multi-line).

All four are optional. Clearing one saves `null`, and the login page then omits that text.

#### Links

Card **Links**, description **https:// or a relative path such as /privacy. Empty links are hidden.** Fields **Privacy policy URL**, **Terms of service URL**, **Help URL**. Rule (`isSafeLink`, mirrors the server's `validate_link`): `https://host/...` or a site path starting with a single `/`. The rule rejects `http:`, `javascript:`, `data:`, `//host`, spaces, backslashes and credentials. Error: **Must start with https:// or be a relative path such as /privacy.** In the preview footer they appear as **Privacy Policy**, **Terms of Service** and **Help**.

#### Chat sample questions

Card **Chat sample questions**, description **Suggested prompts on an empty chat (1 to 8).** It is an ordered list with move up / move down / remove buttons, **Add question**, and an `n / 8` counter. Rules: at least one question, at most 8, none empty, each at most 200 characters and one line. Errors: **Add at least one sample question.**, **At most 8 questions.**, **Questions cannot be empty.**, **Each question is at most 200 characters.**, **Use one line per question.**

#### Live preview

Panel **Live preview**. The right side of its header shows **Unsaved changes** while the form differs from the baseline, otherwise **Version N** (the server's branding version). The preview renders the unsaved draft in two panes, **Light** and **Dark**, side by side, whatever theme the page is in. Each pane shows:

- a sidebar header with the logo and an active nav row in the brand colour;
- the login card: brand panel with mark, eyebrow, headline, supporting copy and "© year company", plus the sign-in card with **Sign in with Microsoft**, the access contact, and the footer links;
- a chat exchange: the first sample question as a user bubble and an assistant reply signed with the assistant name.

The preview uses the derived ramp from the primary colour. Accent and tint do not change the preview.

#### Action bar

A sticky bar at the bottom:

- **Reset to defaults**: confirm **Reset branding to defaults?** with the text "Names, colours, logos, login copy, links and sample questions go back to the seeded defaults for everyone. Uploaded files stay stored." It sends every field in one PUT. See [Known limitations](#known-limitations-and-in-flight-changes) for how this differs from the seeded row.
- **Discard** (only when dirty): restores the loaded values.
- **Save changes** (shows **Saving…** while pending): disabled when nothing changed. If the form has errors, the click only reveals the errors.

After a save:

- The toast reads **Branding saved** (or **Branding reset to defaults**): "The new brand is live for everyone."
- Your own browser applies the new brand at once (`useBrandUpdater().setBrand`).
- The server bumps `version`, writes an audit row `branding_updated` with the changed field names and from/to versions (no values), clears its in-process cache, and broadcasts `cache.invalidate/branding` so other workers drop theirs.

Errors:

- **409 (stale version)**: toast **Branding was changed by someone else** / "The latest version has been loaded. Review it and re-apply your changes." The form reloads the latest values and **your edits are discarded**.
- **422**: the server's reason appears in a red banner above the form. Examples: a contrast failure ("Primary colour #xxxxxx fails WCAG AA contrast: ..."), an unknown asset id, or a field error.
- Other errors: banner "Saving failed: `<message>`".
- If the initial load fails: **Could not load branding** with a retry button and the request id.

How a saved brand reaches users:

- Server-rendered shell (layout, metadata, CSS variables) via `frontend/lib/brand-server.ts::fetchBrand`. This is a server-side `GET /branding` with Next's data cache, revalidated every 300 s and capped at 2.5 s. On any failure the server falls back to the built-in defaults.
- Client refresh: `lib/brand-context.tsx` can re-read `/api/proxy/branding`.
- Backend `GET /branding`: a per-process cache of 60 s (`BRANDING_CACHE_TTL_SECONDS`) and HTTP `max-age` 300 s with ETag/304 (`BRANDING_HTTP_MAX_AGE_SECONDS`). If the database is unreachable, the config defaults are returned and not cached.

### Insights Taxonomy tab

Source: `frontend/components/admin/settings/TaxonomyTab.tsx`; backend `backend/app/api/routes/insights.py` (taxonomy routes), `backend/app/insights/taxonomy_service.py`, `backend/app/insights/taxonomy.py`.

- The list loads `GET /admin/insights/taxonomy?include_archived=true` once (no refetch on focus).
- Sub-tabs: **Intents** (**What the visitor asked about**), **Bot quality** (**How well the assistant answered**) and **Issues** (**Why an answer fell short**). Each badge counts active entries.
- **Show archived (n)** checkbox: archived rows appear dimmed and cannot be edited.
- Help line: **Codes are permanent once created, so labels already stored on conversations never break. Archive an entry to remove it from filters and the classifier; conversations already labelled keep showing its label.**

There is no "topic" kind. What other parts of the product call a topic maps to an **Intent** here (with an optional **Group** for roll-ups).

#### Table

| Column | What it does |
|---|---|
| **Order** | Up / down arrows. A move renumbers the whole kind in steps of 10 (max 10,000) and PATCHes every row whose order changed. Hidden archived rows are skipped over. |
| **Code** | Read-only with a lock icon (tooltip **Codes cannot be changed after creation**). |
| **Label** | Editable, required, max 128 characters. |
| **Colour** | Picker. It shows **Default** when no colour is set (the brand 600 colour is used). |
| **Group** | Intents only. Editable, max 64 characters (for example **Customer / prospect**). |
| **Status** | **Active** or **Archived**, plus a **Core** tag for protected codes. |
| Actions | **Save** appears when the row has edits. The archive icon is disabled for core codes, with the tooltip **Core code used by the rule stage; it cannot be archived**. |

Row errors are shown as a tooltip on Save and as a toast: **Label is required.**, **Label is at most 128 characters.**, **Colour must be #RRGGBB.**, **Group is at most 64 characters.** Success toast: **`<CODE>` saved**.

Archive asks **Archive `<CODE>`?** with "It disappears from filters and is no longer offered to the classifier. Conversations already labelled keep their label. Entries are never deleted, and the code cannot be reused." The toast then reads **`<CODE>` archived**. There is no unarchive action in the UI or the API.

Protected (core) codes, from `PROTECTED_CODES` (frontend mirror `PROTECTED_TAXONOMY_CODES`). The server answers 409 "This code is used by the rule stage and cannot be archived.":

- Intents: `GRE`, `ACK`, `OOS`, `INJ`, `AMB`, `OTHER`
- Bot quality: `OK`, `CORR`, `SCOPE`, `PART`, `FB`, `CLAR`, `OFF`, `FALSE`, `ACK`
- Issues: `LLM_FAIL`, `OTHER`

The seeded defaults (`DEFAULT_ENTRIES`) are 13 intents (`SVC`, `SEC`, `RAI`, `CO`, `CON`, `BIZ`, `SUP`, `OOS`, `INJ`, `AMB`, `OTHER`, `GRE`, `ACK`), 9 bot-quality values and 10 issues. The live database may differ.

Empty state for a kind: **No `<intent | bot-quality value | issue>` entries** / **Add the first one below.**

#### Add entry form

Heading **Add intent** / **Add bot-quality value** / **Add issue**. Fields:

- **Code**: placeholder `e.g. BILLING`. Input is upper-cased and stripped to A-Z, 0-9 and `_` as you type. It must match `^[A-Z][A-Z0-9_]{0,31}$`.
- **Label**: required.
- **Colour (optional)**: `#RRGGBB`.
- **Group (optional)**: intents only.

The **Add** button shows **Adding…** while pending. The new entry is placed at the end of the order (highest order + 10).

Errors:

- "Code must start with a letter and use only A to Z, 0 to 9 and _ (at most 32 characters)."
- "`<CODE>` already exists for this kind (archived codes included); codes are never reused." This message also covers a server 409.
- **Label is required.**
- **Colour must be #RRGGBB.**

The footer hint is **The code is permanent.** plus a kind-specific line:

- Intents: "Group intents (e.g. “Customer / prospect”) to roll them up in Analytics."
- Bot quality: "New values count as not handled until the classifier is updated."
- Issues: "Issues explain why an answer fell short."

The form does not expose `description`, `substantive` or `handled`. New entries get the API defaults: `substantive = true`, `handled = false`, no description.

#### Effect on classification

- The classifier reads the active entries from `insight_taxonomy` fresh on every run (`InsightsRepository.load_taxonomy`, no cache). If the table has no active intents or qualities, it uses `DEFAULT_TAXONOMY`.
- The LLM prompt lists each active code as `- CODE: Label (Group)`, and the JSON schema restricts answers to those codes (`backend/app/insights/llm.py`). Descriptions are not sent. Renaming a label or changing a group therefore changes the guidance for future classifications.
- Output validation:
  - An intent code the model returns that is not active becomes `OTHER`.
  - An unknown bot-quality value becomes the rule-based prior.
  - An unknown issue is dropped.
  - All of these lower the label's confidence.
- `substantive` comes from the intent entry. A turn counts as `handled` only when its bot-quality entry has `handled = true` (and, for `OK`, the answer was grounded). A new bot-quality value therefore counts as not handled, which lowers handled-% in Analytics if the model uses it.
- **No re-classification.** Edits, additions and archives only affect exchanges classified afterwards. Classification skips turns that already have a label (`insert_labels` uses `ON CONFLICT DO NOTHING`; `pending_turns` selects unlabelled turns only). The backfill script `backend/scripts/backfill_turn_insights.py` also only queues unlabelled history. To relabel old conversations, delete their `turn_insights` rows and run the backfill. Doing this costs LLM tokens under the daily insights budget and is not available in the UI.
- Archived codes still resolve to their label wherever stored labels are shown (`taxonomyLabel` keeps archived entries). They disappear from filter pickers (`taxonomyFilterOptions`, active only).
- Every create, update and archive writes an audit row in the same transaction: `insights_taxonomy_created`, `insights_taxonomy_updated` (before/after values, PII-masked) or `insights_taxonomy_archived`.

## Common tasks

### Change branding (for example a new customer)

1. Open **Settings** (Branding is the default tab).
2. In **Identity**, set **Product name**, **Assistant name**, **Company name** and **Support email**. Set **Access contact email** if access questions should go somewhere else.
3. In **Colours**, enter the customer's primary hex. Wait for both **Light** and **Dark** badges to show **AA**. If either fails, the warning tells you whether to go darker or lighter. Leave **Accent** and **Tint** empty to use the derived values, unless the customer specifies them.
4. In **Logos**, drop the light and dark logos, the marks and the favicon (PNG, WebP or SVG, max 512 KB, max 4096 px). Check each tile and the **Live preview** (both panes).
5. Update the **Login page** copy, **Links** and **Chat sample questions**.
6. Click **Save changes**. Your browser updates at once. Other users get the new brand within about 5 minutes (see limitations), or straight away on a fresh page load once caches expire.
7. To confirm, open the login page in a private window, or check the Governance audit log for `branding_updated`.

### Swap only the logo

1. **Logos** > drop the new file on **Logo (light)** (and **Logo (dark)** if it differs).
2. Click **Save changes**. Until you save, the uploaded file is stored but not used.

### Add a taxonomy intent (a new "topic")

1. Open **Settings** > **Insights Taxonomy** (`/admin/settings?tab=taxonomy`). **Intents** is selected.
2. Check that the code is not already taken. Tick **Show archived** too, because archived codes cannot be reused.
3. In **Add intent**, enter **Code** (for example `BILLING`), **Label** (for example "Billing and invoices"), and optionally a **Colour** and a **Group** (use an existing group such as **Customer / prospect** so it rolls up in Analytics).
4. Click **Add**. The toast shows **BILLING added**.
5. Use the arrows to move it near related intents if the order matters.
6. New conversations can now be labelled `BILLING`. Existing conversations keep their current labels (see [Effect on classification](#effect-on-classification)).

### Rename or recolour an existing label

1. Find the row, edit **Label**, **Colour** or **Group**, then click the row's **Save**.
2. Every stored label with that code now shows the new text and colour. Future classifications use the new label text as guidance.

### Retire an intent

1. Click the archive icon on the row (not possible for **Core** codes), then confirm **Archive**.
2. The intent disappears from filters and the classifier. Past conversations keep showing it. This cannot be undone from the UI.

## Interactions and states

- **Loading**: skeleton cards (Branding) or skeleton rows (Taxonomy).
- **Load errors**: **Could not load branding** or **Could not load the taxonomy**, each with a retry button and the request id when the server sent one.
- **Unsaved branding edits** are lost if you navigate away. There is no leave-page warning. A 409 also drops them on purpose.
- **Taxonomy edits** are per row. Unsaved row drafts are lost when you leave the page.
- **Reorder** is optimistic. If any PATCH fails, the order is restored and the toast reads **Could not reorder**.
- **Keyboard**: every control is a native input or button. Drop zones also open the file picker on Enter or Space. Ctrl/Cmd+K opens the command palette.
- **Exports**: none.
- **What to do when data looks wrong**: if the brand looks old for other users, wait out the caches (up to about 5 minutes) or reload. If Analytics shows a code instead of a label, that code has no entry in the taxonomy. Add one with that exact code.

## Light and dark theme

- The brand ramp drives both themes. `brand-600` is the light-theme link and text colour on white, and `brand-400` is the dark-theme one on the dark background. That is why both must pass 4.5:1 before the server accepts a primary colour.
- Provide separate dark logos and marks when the light artwork would vanish on dark surfaces. Otherwise the built-in default dark artwork is used for those slots.
- The live preview always shows both themes, independent of the page theme.
- Taxonomy colours are used as-is in charts in both themes. No contrast check is applied.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/branding` | public (no auth) | Load the form; also used by the server layout and brand refresh |
| PUT | `/admin/branding` | `branding:write` | Save and Reset (partial update, `version` required; 409 stale, 422 invalid or contrast) |
| POST | `/admin/branding/assets` | `branding:write` | Upload a logo, mark or favicon (multipart `file`; 413, 415, 422) |
| GET | `/branding/assets/{sha}` | public | Serve brand images (through the Next route `/branding/assets/[sha]`) |
| GET | `/admin/insights/taxonomy?include_archived=true` | `dashboard:read` | List all taxonomy entries |
| POST | `/admin/insights/taxonomy` | `taxonomy:write` | Add an entry (409 duplicate) |
| PATCH | `/admin/insights/taxonomy/{kind}/{code}` | `taxonomy:write` | Edit label, colour, group or order |
| POST | `/admin/insights/taxonomy/{kind}/{code}/archive` | `taxonomy:write` | Archive (409 for core codes) |

Browser calls go through `/api/proxy/...`. The asset images go through `/branding/assets/<sha>` on the Next origin.

## Related pages

- [README.md](README.md): walkthrough index
- [shell.md](shell.md): sidebar logo, favicon, theme toggle
- [login.md](login.md): where the login copy, links and access contact appear
- [chat.md](chat.md): assistant name and sample questions
- [analytics.md](analytics.md): where intents, bot quality and issues are charted
- [conversation-logs.md](conversation-logs.md): per-conversation insight labels
- [governance.md](governance.md): audit log rows for branding and taxonomy changes
- [user-management.md](user-management.md): the other superadmin page
- [dashboard.md](dashboard.md), [documents.md](documents.md), [visitors.md](visitors.md), [usage-cost.md](usage-cost.md), [feedback.md](feedback.md), [evaluation.md](evaluation.md)

## Known limitations and in-flight changes

- **"Live for everyone" is not instant for other users.**
  - Your own browser updates immediately.
  - Other users' server-rendered shell uses `fetchBrand` with a 300 s revalidate.
  - Browsers may also cache `GET /branding` for 300 s.
  - The backend per-process cache (60 s) is invalidated on save.
  - Expect up to about 5 minutes.
- **Reset to defaults does not exactly match the seeded row.** The frontend reset (`defaultBrandingForm`):
  - sends empty login eyebrow, headline, supporting copy and description, which are saved as `null`, so the seeded login copy is cleared rather than restored;
  - sends accent and tint as `null` (derived from `#008080`), where the seed stores `#00584f` and `#eaf6f5`;
  - sends names and support email from the frontend `DEFAULT_BRAND`, not from the backend `BRAND_*` settings.
  - Not verified whether the derived accent and tint equal the seeded values.
- Uploaded brand assets are never deleted, and there is no asset library or garbage collection.
- The 4096 px dimension limit is enforced only by the server. The UI hint mentions type and size only.
- Accent and tint contrast badges are advisory. The server checks the primary only.
- Taxonomy:
  - no unarchive;
  - `description`, `substantive` and `handled` cannot be edited in the UI;
  - reorder sends one PATCH per moved row (not atomic);
  - no in-app re-classification of existing conversations;
  - the list endpoint needs only `dashboard:read`, so operators can read the taxonomy through the API.
- The Settings nav item is gated on `branding:write` only. The page itself opens with either `branding:write` or `taxonomy:write`. Both belong to superadmin today, so this has no visible effect.
- Coming in branch `fix/chat-page-fetch-timeouts` (uncommitted):
  - `frontend/lib/brand-server.ts` replaces Next's fetch data cache with an in-process cache (300 s). A stale brand is served at once while one bounded (2.5 s) refresh runs in the background. If the backend is down, the last good brand is kept instead of reverting to defaults.
  - Backend `GET /branding` loads through its own session with a `BRANDING_LOAD_TIMEOUT_SECONDS` bound (default 5 s) and returns config defaults on timeout.
  - The propagation delay to other users stays up to about 5 minutes.
- Coming in branch `feat/askdiq-chat-widget`: the **Assistant name** gains more visibility. It drives the floating launcher ("Ask `<Assistant name>`", greeting "Hi, I'm `<Assistant name>` — ask me anything"), the chat drawer title, the composer label and the Chat page heading (which now shows the assistant name instead of the product name). Choose it with that in mind.
- Coming in branch `fix/shell-nav-logout-theme-dates`: the theme toggle offers only Light and Dark (System mode removed). The Settings preview already shows both, so this page is unaffected.
