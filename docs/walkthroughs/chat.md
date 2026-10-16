# Chat

> Ask the assistant questions that are answered from the knowledge base, rate the answers, and manage your saved chats.  Route: `/chat` · Nav: **Chat** · Permission: none beyond sign-in (every signed-in user who is not suspended: `active`, `pending`, `rejected`, with or without a role)

## Purpose and who uses it

Chat is the end-user surface of the product: a question box backed by the retrieval-augmented
generation (RAG) pipeline. It is the only page that every signed-in user can open:

- **Users without an approved role** (status `pending` or `rejected`, or `active` with no role) see a
  sidebar that holds Chat only. Any dashboard route shows Access Required instead.
- **Operators, admins and superadmins** use Chat to try questions themselves, check how answers look,
  and jump from an answer to its governance trace (**View trace**).
- **Support / analysts** reproduce a user's question here, then look it up in Conversation Logs,
  Usage & Cost or Governance.

Suspended users never get here: the route gate shows the suspended page, and the backend refuses every
chat endpoint with `403 Account suspended.`

Source: `frontend/lib/auth-gate.ts`, `backend/app/auth/deps.py::require_role_or_chat`,
`backend/app/auth/permissions.py` (`CHAT_STATUSES = {ACTIVE, PENDING, REJECTED}`).

## How to get there

- **Sidebar:** **Chat** (the item has no permission, so every signed-in user sees it).
- **URL:** `/chat`. The page reads no query or deep-link parameters; you cannot open a specific
  session by URL.
- **After sign-in:** active users go to `next` (or `/`); everyone else lands on `/chat` unless `next`
  itself is a chat route (`frontend/lib/auth-gate.ts::postLoginPath`).
- **Command palette** (Ctrl / Cmd + K): the palette lists the nav items, so typing "chat" finds it.
  The item has no extra keywords.

## Layout at a glance

```text
+----------+-------------------------------------------------------------+
| Sidebar  | [+ New chat]  | [logo] DeployIQ Assistant         [AB Name] |
| (shell)  |               |  Answers are grounded in your knowledge ... |
|          | Chat title 1  |---------------------------------------------|
|          |  5m ago       |                                             |
|          | Chat title 2  |              Ask DeployIQ anything          |
|          |  2d ago ·     |   Try a question about Essential Eight, ... |
|          |  expired      |   [sample question 1] [sample question 2]   |
|          |   (pencil,    |   [sample question 3] [sample question 4]   |
|          |    trash on   |                                             |
|          |    hover)     |   ...or the message thread once you ask     |
|          |               |---------------------------------------------|
|          |  256 px list  | [Ask about your security and compl...] [>]  |
+----------+---------------+---------------------------------------------+
```

> Screenshot placeholder: `img/chat-empty.png` — /chat with the session list on the left, the branded header, the empty state and the four sample-question buttons.

> Screenshot placeholder: `img/chat-thread.png` — a thread with one user bubble, one answer showing **Helpful** / **Unhelpful** / **Copy** / **Delete**, and **View trace** below it (admin view).

> Screenshot placeholder: `img/chat-thumbs-down.png` — the **What went wrong?** panel open under an answer with two reason chips selected.

## Layout tour

### Session list (left column)

A fixed 256 px column (`w-64`) on the left of the page.

- **New chat** button (top). Calls `POST /sessions` and makes the new, empty session active.
- One row per saved chat, newest activity first (backend orders by `last_activity_at DESC`).
  - Title: the session title. A new session is titled `New chat` until its first question; the first
    question then becomes the title, cut to 80 characters (`backend/app/core/sessions.py::record_exchange`).
  - Sub-line: relative age of the last activity: `just now`, `Nm ago`, `Nh ago`, `Nd ago`
    (rounded), plus ` · expired` when the session has expired.
  - On hover, two icon buttons appear: pencil (tooltip **Rename**) and trash (tooltip **Delete**).
- Empty list copy: **No chats yet — start one above.**

How it's computed:

- Endpoint: `GET /sessions` (`backend/app/api/routes/sessions.py::list_sessions`). Only your own
  sessions (matched by your email), all of them, no paging.
- `is_expired` = `expires_at < now (UTC)`. `expires_at` is set to last activity +
  `SESSION_TIMEOUT_MINUTES` (default **60**), and is pushed forward on every exchange.
- Relative time: the server's naive UTC timestamp is parsed as UTC (`parseServerDate`) and compared
  with the browser clock, so the age is timezone-independent.
- Refresh: loaded once when you sign in (the provider lives in the app layout, not the page), and
  reloaded after every answered question. There is no polling; renames, deletes and new chats update
  the list locally.

Rename: click the pencil, edit inline, **Enter** or click away to save, **Esc** to cancel. An unchanged
or blank title is not sent. `PATCH /sessions/{id}` stores up to 256 characters.

Delete: confirms with the browser dialog **Delete this chat? This can't be undone.** then
`DELETE /sessions/{id}` removes the session and its messages. If it was the open chat, the next most
recent one opens, or the page goes blank.

Failures of open / new / rename / delete show an error toast: **Couldn't open that chat**,
**Couldn't start a new chat**, **Couldn't rename the chat**, **Couldn't delete the chat**, with the
backend's message (or **Please try again.**) as the description.

Source: `frontend/components/ChatSessionList.tsx`, `frontend/app/(app)/chat/page.tsx`,
`frontend/lib/chat-context.tsx`.

### Header

- Brand mark (logo) and title **`{productName} Assistant`** (default **DeployIQ Assistant**; the
  product name comes from Settings > Branding via `/branding`).
- Subtitle: **Answers are grounded in your knowledge base and checked before delivery.**
- Right: user chip with your initials, name and email (`aria-label` "Signed in as ..."); the name and
  email are hidden below the `sm` breakpoint.

Source: `frontend/components/chat/ChatHeader.tsx`.

### Empty state and sample questions

Shown when the open chat has no messages:

- Heading **Ask {productName} anything** (default **Ask DeployIQ anything**).
- Text **Try a question about Essential Eight, Zero Trust, governance, or how the platform works.**
- Sample-question buttons (two columns from `sm` up). Clicking one sends it at once.
  They come from branding (at most 8, each cut to 300 characters; `frontend/lib/brand.ts`); the
  four defaults are:
  - `What is the Essential Eight and how does DeployIQ implement it?`
  - `How does DeployIQ keep humans in the loop over AI actions?`
  - `What does the Zero Trust Professional plan include?`
  - `How does DeployIQ handle customer data?`

Source: `frontend/lib/brand.ts::defaultChatSampleQuestions`.

### Message thread

- **Your question:** right-aligned brand-coloured bubble, plain text. Below it: **Copy** (turns into
  **Copied** for 1.5 s) and **Delete**.
- **Pending answer:** three bouncing dots (`aria-label` "Thinking"). There is no token streaming:
  the whole answer appears at once when `POST /query` returns.
- **Answer:** left-aligned card. Light formatting only (`frontend/lib/format-text.tsx`): `**bold**`,
  lines starting with `- ` or `• ` become bullets, `#`/`##`/`###` lines become headings, and a short
  line ending in `:` (80 characters or fewer) is shown bold and turns the following lines into a
  bullet list until a blank line. No links, tables, code blocks or Markdown links are rendered.
- **Error answer:** red text in the answer card (see "Errors and timeouts" below). Error answers have
  no feedback or copy buttons.
- Under each answer: **Helpful**, **Unhelpful**, **Add note** (after a rating), then on the right
  **Copy** and **Delete**.
- **View trace** (with an arrow icon) under each answer, only for users holding `dashboard:read`
  (operator and up). It opens `/admin/governance?tab=Traces&request_id=<id>`. Users without a role
  never see it. It works for live answers and for answers reloaded from history (the saved message
  keeps its `request_id`).
- The thread auto-scrolls to the bottom when messages change.

Citations / sources: the `/query` response carries a `sources` list (chunk id, document id, excerpt,
score, metadata), but **the current `/chat` page does not display it**. Sources are kept for the
governance trace (chunk ids, document ids and scores go into the `query` audit event). Source chips
under answers are coming in branch `feat/askdiq-chat-widget` (see below).

Delete exchange: **Delete** on either bubble removes that question and its answer (matched on the
server by their shared `turn_index`) via `DELETE /sessions/{sid}/messages/{mid}`. The pair disappears
at once; a failed server delete is ignored silently, so the pair can come back on reload.

Source: `frontend/components/MessageBubble.tsx`, `frontend/components/chat/TraceLink.tsx`,
`frontend/lib/entity-links.ts`.

### Composer

- Textarea with placeholder **Ask about your security and compliance posture…**
- **Enter** sends, **Shift+Enter** adds a new line.
- Send button (`aria-label` **Send**) is disabled while a question is in flight or the box is empty.
  Only one question can be in flight at a time.
- The box has no character counter; the backend rejects questions longer than **2,000** characters
  (`QueryRequest.query`, `max_length=2000`) with a 422, shown as **Request validation failed.**

### Feedback: Helpful / Unhelpful

What you can do:

1. Click **Helpful** (thumbs up). It lights green at once and a toast says **Thanks for the feedback**.
2. Click **Unhelpful** (thumbs down). It lights red and the **What went wrong?** panel opens:
   - Reason chips (toggle, multi-select): **Wrong**, **Incomplete**, **Off topic**, **Unsafe**,
     **Too slow**, **Unclear**.
   - **What should it have said? (optional)** with a `n/4000` counter and placeholder
     **The answer you expected. Do not include personal information.**
   - **Skip** closes the panel (Esc does too); **Send feedback** (shows **Sending…**) re-sends the
     rating with the reasons, then toasts **Feedback sent** / **Thanks, this helps us improve the
     answers.**
   - Clicking **Unhelpful** again while it is active only re-opens or closes the panel.
3. After rating, **Add note** (or **Edit note**) opens a small box: **Why was this answer helpful?** /
   **Why was this answer unhelpful?**, a `n/1000` counter, placeholder
   **Optional: Tell us what went well or what could be improved. Do not include personal information.**,
   **Cancel** and **Save note** (**Saving…**). Saving toasts **Note saved**.

What gets recorded:

- Endpoint: `POST /sessions/{session_id}/messages/{message_id}/feedback`
  (`backend/app/api/routes/sessions.py::submit_feedback`), guard `require_role_or_chat`.
- Values: thumbs up = **rating 5**, thumbs down = **rating 1** (`THUMBS_UP = 5`, `THUMBS_DOWN = 1`).
  The API accepts 1-5, but Chat only ever sends 5 or 1.
- One row per (assistant message, user) in `message_feedback`; a resubmission is an upsert that
  replaces rating, comment, reason tags and corrected answer and bumps `version`
  (`backend/app/feedback/repository.py::upsert_chat_feedback`). New rows start with triage status `new`.
- `comment` max 1,000 characters (control characters stripped); `reason_tags` must be from the allow
  list above; `corrected_answer` max 4,000 characters and **PII-masked before storage**.
- An audit event `user_feedback` is written with rating, PII-redacted comment, reason tags,
  `has_corrected_answer`, conversation id and message id.
- Spike alert: after each rating, if at least **5** ratings were made in the last hour and more than
  **25%** of them are 2 or lower, an audit event `negative_feedback_spike` is recorded
  (`backend/app/core/sessions.py::record_feedback`).
- Rate limit: **30 feedback submissions per minute** per user and per IP.
- Only assistant messages in your own session can be rated (404 otherwise).

Where it shows up later:

- **Feedback** page (`/admin/feedback`): the row appears with rating, comment, reason tags, corrected
  answer and the exchange. Deep link: `/admin/feedback?message_id=<assistant message id>`.
  The dashboard counts ratings of 4 or more as positive and 2 or less as negative
  (`frontend/lib/feedback-api.ts::POSITIVE_MIN / NEGATIVE_MAX`). See [feedback.md](feedback.md).
- **Conversation Logs** (`/admin/conversations/<session id>`): under each turn, a **Feedback** block
  with a `5 / 5` or `1 / 5` chip, reason tags, the comment in quotes, and **Open in Feedback**.
  See [conversation-logs.md](conversation-logs.md).
- **Dashboard** activity feed: `Feedback received: 5/5` (plus ` with a comment`) and
  `Negative feedback spike detected` (`backend/app/dashboard/activity.py`).
- **Governance** audit log: `user_feedback` and `negative_feedback_spike` (the latter at level `ALERT`).

After a reload, the thumbs state comes back (the session detail returns your `feedback_rating` per
message), but your earlier comment, reasons and corrected answer are not shown.

## How an answer is produced (what the user sees)

`POST /query` (`backend/app/api/routes/query.py::query`, pipeline
`backend/app/harness/orchestrator.py::run_query`):

1. **Session:** the request carries the open `session_id`. If it is unknown, someone else's, or
   expired, the server silently starts a new session (audit `session_rotated`). With no id, it resumes
   your most recent non-expired session **from the same IP**, or starts a new one.
2. **History:** the last **20** messages of the session (`SESSION_HISTORY_MAX_MESSAGES`) are loaded;
   only follow-up questions fold history into the prompt (at most 6 turns).
3. **Small talk** (greetings, thanks, "what can you do"): a canned reply, outcome `conversational`.
4. **Pre-checks:** length, unsafe input, spam, unclear input. Blocked questions get a polite fixed
   reply (outcome `blocked`), for example:
   - `unclear_input`: "Your question appears too short or incomplete. Could you please provide a bit
     more detail or ask a specific question about {brand}?"
   - `spam_detected`: "I couldn't quite understand your question. Could you please rephrase ..."
   - `unsafe_input_detected`: "I am unable to process that request. ..."
5. **Retrieval:** top **5** chunks (`DEFAULT_TOP_K`). If none, or the best score is below
   **0.15** (`MIN_RETRIEVAL_SCORE`): "I couldn't find relevant information in the {brand} knowledge
   base to answer that. ..." (flag `off_topic`). Chunks that look like prompt injection are dropped.
6. **Generation** (Azure OpenAI, request timeout **60 s**, one retry on error or empty output).
7. **Groundedness check** (threshold **0.45**). An answer that fails is **not shown**; the user gets
   the generic fallback "Seems like you have made a typo, could you please rephrase your question? I
   can help with questions about ..." (outcome `ungrounded`). The same fallback text is shown when
   the LLM call fails (outcome `llm_failed`).
8. The answer returns at once; the message pair, cost and `query` audit event are written in the
   background, followed by online evaluation and insight classification.

`{brand}` above is the backend `BRAND_NAME` setting (default `DeployIQ`), not the frontend branding.

## Common tasks

1. **Ask a question and check its trace (admin/operator).**
   Open **Chat**, type the question, press **Enter**. When the answer arrives, click **View trace**
   under it to open Governance > Traces filtered to that `request_id` (retrieval scores, outcome,
   tokens, cost).

2. **Report a bad answer with a correction.**
   Click **Unhelpful**, pick one or more chips (for example **Wrong**, **Incomplete**), type the
   expected answer in **What should it have said? (optional)**, click **Send feedback**. An admin
   will see it on the Feedback page with status **New**, ready to triage or promote to an eval case.

3. **Find a user's feedback from Chat-side ids.**
   In Conversation Logs open the session, find the turn's **Feedback** block, click
   **Open in Feedback**. Or go straight to `/admin/feedback?message_id=<assistant message id>`.

4. **Start fresh / tidy up.**
   Click **New chat** to start a clean session (no history is carried into it). Use the pencil to give
   a chat a meaningful title, or the trash icon to delete it permanently. To remove one bad
   question/answer but keep the rest, click **Delete** under either bubble.

5. **Help a pending user.**
   A user who just signed in and is awaiting approval lands on `/chat` with only **Chat** in the
   sidebar. They can chat normally; once a superadmin approves them in User Management, the dashboard
   unlocks without a re-login (toast **Access approved**).

## Interactions and states

| Element | Action | Result |
|---|---|---|
| Sample question | Click | Sends that question immediately |
| Composer | Enter / Shift+Enter | Send / new line |
| Session row | Click | Loads that session (`GET /sessions/{id}`) |
| Pencil | Click, then Enter / Esc / blur | Save / cancel / save rename |
| Trash | Click + confirm | Deletes the session |
| **Copy** | Click | Copies the bubble text; label shows **Copied** for 1.5 s |
| **Delete** (bubble) | Click | Removes the question/answer pair (no confirm) |
| **Helpful** / **Unhelpful** | Click | Optimistic rating; rolled back on failure |
| **Add note** | Click | Opens the note box; Esc closes it |
| **View trace** | Click | Governance > Traces for this request (operator and up) |

Keyboard: Enter / Shift+Enter in the composer; Enter / Esc in the rename field; Esc closes the note
box and the **What went wrong?** panel (focus starts on the first chip). There is no chat-specific
global shortcut in this version.

Loading: on first load the latest session opens automatically. While its messages load there is no
spinner; the previous content stays until the new one arrives.

Empty states: **No chats yet — start one above.** (session list) and the **Ask {productName} anything**
panel (thread).

### Errors and timeouts

| Situation | What the user sees |
|---|---|
| More than **10 questions/min** for your account (`RATE_LIMIT_PER_MINUTE`) | Answer card in red: `Rate limit exceeded (per user): max 10 questions per minute. Try again in Ns.` |
| More than **30 questions/min** from one IP (`RATE_LIMIT_PER_MINUTE_PER_IP`) | Same, with `(per IP)` and `max 30` |
| All LLM slots busy (`LLM_MAX_CONCURRENCY`, default 64 per process) | **The assistant is at capacity. Please retry shortly.** (429, `Retry-After: 2`) |
| Question over 2,000 characters | **Request validation failed.** |
| Session deleted elsewhere / not yours | Opening it falls back to a blank chat; sending silently starts a new session |
| Backend down / unreachable | The proxy returns 502 with `Backend service is unreachable at <url> (<code>). Please ensure the FastAPI backend server is running.` |
| Unhandled backend error | **An unexpected error occurred. Quote the request_id when contacting support.** |
| Network failure before any HTTP response | **DeployIQ couldn't answer that just now. Please try again.** (hard-coded name) |
| Account suspended mid-session | `Account suspended.` (403) |
| Feedback failure | Red line under the answer with the server message or **Failed to submit rating. Please try again.**; the thumb rolls back |
| Rating with no saved chat open | **Open a saved chat to rate its answers.** |

There is no **Retry** button in this version: retype or paste the question. Rate-limit hits are
recorded as `rate_limited` audit events.

Timeouts in this version: the Next proxy (`frontend/app/api/proxy/[...path]/route.ts`) sets **no
timeout** of its own, so a hung backend leaves the dots spinning until the Node/undici defaults give
up. The backend bounds the LLM call at 60 s per attempt (`AZURE_OPENAI_REQUEST_TIMEOUT`), with at most
one retry. Bounded proxy timeouts are coming in branch `fix/chat-page-fetch-timeouts` (see below).

Navigating away while an answer is pending is safe: chat state lives in `ChatProvider` in the app
layout, so the answer is still there when you come back to `/chat`.

## Light and dark theme

Both themes are supported. Your bubbles use the brand colour with white text in both. The active
**Helpful** state is emerald and **Unhelpful** is rose, with lighter tints in dark mode. Error
answers are red-600 (light) / red-400 (dark).

## API endpoints used

All calls go through the Next proxy `/api/proxy/...`. Every chat endpoint is guarded by
`require_role_or_chat` (signed in and status `active`, `pending` or `rejected`); none needs a
dashboard permission.

| Method | Path | Permission | Used for |
|---|---|---|---|
| POST | `/query` | `require_role_or_chat` + per-user/IP rate limit + LLM capacity gate | Ask a question; returns answer, sources, ids, tokens, cost |
| GET | `/sessions` | `require_role_or_chat` | Session list |
| POST | `/sessions` | `require_role_or_chat` | **New chat** |
| GET | `/sessions/{session_id}` | `require_role_or_chat` | Open a session (messages + your ratings) |
| PATCH | `/sessions/{session_id}` | `require_role_or_chat` | Rename |
| DELETE | `/sessions/{session_id}` | `require_role_or_chat` | Delete a session |
| DELETE | `/sessions/{session_id}/messages/{message_id}` | `require_role_or_chat` | Delete one question/answer pair |
| POST | `/sessions/{session_id}/messages/{message_id}/feedback` | `require_role_or_chat` + 30/min feedback limit | Thumbs, reasons, corrected answer, note |
| GET | `/branding` | public | Product name and sample questions |

The **View trace** link leads to Governance, which needs `dashboard:read`.

## Related pages

- [README.md](README.md) · [shell.md](shell.md) · [login.md](login.md)
- [feedback.md](feedback.md): triage of the ratings given here
- [conversation-logs.md](conversation-logs.md): full transcripts of chat sessions
- [usage-cost.md](usage-cost.md): per-query tokens and cost
- [governance.md](governance.md): traces and the audit log
- [analytics.md](analytics.md): intent / quality labels assigned to answers
- [settings.md](settings.md): branding (product name, sample questions)
- [user-management.md](user-management.md): approving pending users

## Known limitations and in-flight changes

Limitations found in code (current `preview/view` at 9115c45):

- **No streaming.** The answer arrives in one piece when the pipeline finishes.
- **No citations in the UI.** `sources` is returned but not rendered on `/chat`. Reloaded history has
  no sources at all (`ChatMessageOut` has no sources field).
- **No Retry** on a failed answer.
- **Expired sessions.** Opening an expired chat (` · expired`) shows its history, but the next question
  silently goes into a **new** session. The page keeps showing the old messages above the new pair,
  and the old messages can no longer be rated or deleted (the backend looks them up in the new
  session and returns 404).
- **No IP carry-over:** with no session open, the server only resumes a session started from the same
  IP.
- **Generic fallback text.** Ungrounded answers and LLM failures both show "Seems like you have made a
  typo, could you please rephrase your question? ...", which misleads users when the real cause is a
  failed grounding check or an LLM outage. Also, `run_post_checks` uses its own default minimum
  retrieval score of **0.3** while the retrieval gate uses **0.15**, so answers whose best source
  scores between 0.15 and 0.3 are always rejected as `low_retrieval_confidence` after the LLM was
  paid for.
- **Hard-coded name** in the network-error text (`DeployIQ couldn't answer that just now...`),
  regardless of branding.
- **Delete exchange** has no confirmation and no rollback on failure.
- **Notes after reload:** the rating comes back, but the comment, reasons and corrected answer do not.
  Saving a new note on a reloaded thumbs-down sends empty reasons, and the upsert **clears** the
  earlier reason tags and corrected answer.
- **Feedback comments** are PII-redacted in the audit event but stored as typed in the
  `message_feedback` row; only the corrected answer is masked there.
- **Mobile:** the 256 px session list is always visible; on a phone it takes most of the width.
  There is no collapsible history on `/chat`.
- The session list is not paged; all of a user's sessions load at once.

### Coming in branch `feat/askdiq-chat-widget`: AskDIQ floating widget and right drawer

Everything in this subsection is **Coming in branch `feat/askdiq-chat-widget`** (commits `8abae17`
"refactor(chat): share the conversation pane; name the assistant AskDIQ" and `a965b12`
"feat(chat): floating AskDIQ launcher and right-side chat drawer"). It is not on `preview/view` yet.

**Naming: "AskDIQ".** The assistant name comes from branding `assistantName` (backend
`brand_assistant_name`, default **AskDIQ**) instead of the product name:

- `/chat` header title becomes **{assistantName}** (default **AskDIQ**) instead of
  "DeployIQ Assistant".
- Empty state heading becomes **Ask {assistantName} anything** (default **Ask AskDIQ anything**).
- Composer gets `aria-label` **Message {assistantName}**.
- The network-error fallback becomes **{assistantName} couldn't answer that just now. Please try
  again.** (the hard-coded "DeployIQ" goes away).

**Shared conversation pane.** The message list, empty state and composer move into
`frontend/components/chat/ChatConversation.tsx`, used by both `/chat` (`variant="page"`) and the
drawer (`variant="panel"`, full width, single-column sample questions). Both read the same
`ChatProvider`, so they always show the same session. The page's toast wrapper becomes
`use-reported-action.ts` with the same toast titles.

**New on every answer (both surfaces):**

- **Source chips** (`SourceChips.tsx`): one chip per cited document, best score first, labelled with
  the document's `source_name` metadata or **Knowledge base** if missing; tooltip = first 160
  characters of the excerpt. At most 4 chips, then **+N more** (tooltip lists the rest). Rendered as
  escaped text. Shown only on live answers (`message.response.sources`), not on answers reloaded
  from history. Note: the backend also returns sources on some fallback answers (low-confidence
  `off_topic`, `ungrounded`, `llm_failed`), so chips can appear under "I couldn't find relevant
  information..." replies.
- **Timestamps**: local time (`HH:MM`, browser locale and timezone) next to Copy, with the full local
  date-time as the tooltip, on both bubbles.
- **Retry** button on a failed answer: drops the failed pair locally and re-sends the same question
  (`retryExchange`).
- Session-row rename/delete buttons also appear on keyboard focus (`focus-within`), not just hover.

**Launcher placement and pages.**

- A round 64 px mascot button (`/brand/askdiq-mascot-256.webp`), fixed **bottom-right** (16 px from
  the right, 24 px from `sm` up; respects the iOS safe area), above page content (`z-[80]`).
- Mounted in the `(app)` layout, so it appears on **every page inside the app shell**: Dashboard,
  Documents, Visitors, Usage & Cost, Conversation Logs, Feedback, Evaluation, Governance, Analytics,
  User Management, Settings, and the Access Required view shown to users without a role.
- **Hidden on `/chat`** (and `/chat/...`), which already shows the full conversation. Navigating to
  `/chat` closes the drawer and clears the unread dot.
- Not shown on full-screen gate screens (login spinner, suspended page, "We couldn't check your
  session"), because the layout returns before rendering the shell.
- Accessible name **Ask {assistantName}**, becoming **Ask AskDIQ (answering)** while a question is in
  flight and the drawer is closed, or **Ask AskDIQ (new answer)** when an answer landed while closed.
- Hover/focus tooltip (from `sm` up): **Ask AskDIQ** with a `Ctrl /` key hint.
- Idle animation: a slow float and a pulse ring, both turned off when the drawer is open or the user
  prefers reduced motion. Hover tilts, press shrinks.
- Status badges on the button: bouncing "typing" dots while answering with the drawer closed; a
  brand-coloured **unread dot** when an answer arrives while the drawer is closed.
- **Greeting bubble**: 1.2 s after page load, once per browser session, **Hi, I'm AskDIQ — ask me
  anything** with an X (**Dismiss greeting**). Clicking the text opens the drawer. Dismissing it, or
  opening the drawer, stops it for good (localStorage `askdiq.greeting.dismissed`; per-session flag
  in sessionStorage `askdiq.greeting.seen`).

**Keyboard shortcut.** **Ctrl + /** (Windows/Linux) or **Cmd + /** (macOS) opens or closes the drawer
from any app page except `/chat`, even while typing in a field (no Alt/Shift). It is listed in the
shortcuts help (`?`) as "Open or close the assistant chat drawer".

**Drawer behaviour** (`frontend/components/askdiq/AskDiqDrawer.tsx`):

- Slides in from the **right**, full window height (`100dvh`), **420 px** wide (440 px from `lg`),
  rounded left edge. Code-split: the drawer code loads on first hover/focus of the launcher or on
  first open.
- Header: mascot with a green status dot, title **{assistantName}**, subtitle **Thinking…** while a
  question is in flight, otherwise **Grounded in your knowledge base**.
- Header buttons: **Recent chats** (history icon, toggles the session list in the drawer, with
  **New chat**, rename and delete as on `/chat`; choosing a chat returns to the conversation),
  **New chat** (pen icon), **Open full chat** (link to `/chat`), **Minimize** (tooltip
  **Minimize (Esc)**).
- From 640 px up it is **non-modal**: no backdrop, the dashboard behind stays usable, focus moves to
  the composer on open and back to the launcher on close; **Esc** with focus inside minimizes
  (except while renaming a chat).
- Feedback, **View trace** (operator and up), Copy, Delete and Retry work exactly as on `/chat`.

**State between the drawer and `/chat`.** Both use the layout-level `ChatProvider`: same active
session, same messages, same draft text in the composer, same in-flight question. A question asked in
the drawer keeps answering while you change pages; **Open full chat** continues the same session on
`/chat`. Nothing is persisted beyond what the server already stores (sessions and messages); the drawer
open/closed state and the history/chat view reset on a full reload.

**Mobile (below 640 px).** The drawer is **full-width and modal**: a backdrop (tap to close), a focus
trap with initial focus in the composer, and **Esc** closes. The launcher stays bottom-right; the
hover tooltip is hidden.

> Screenshot placeholder: `img/chat-askdiq-launcher.png` — a dashboard page with the AskDIQ mascot launcher bottom-right and the greeting bubble.

> Screenshot placeholder: `img/chat-askdiq-drawer.png` — the right drawer open over the Dashboard, showing an answer with source chips and timestamps.

Source (branch): `frontend/components/askdiq/AskDiqLauncher.tsx`, `AskDiqDrawer.tsx`,
`AskDiqMascot.tsx`, `frontend/components/chat/ChatConversation.tsx`, `SourceChips.tsx`,
`frontend/lib/askdiq-widget.ts`, `frontend/lib/shortcuts.ts`, `frontend/lib/chat-context.tsx`,
`frontend/app/(app)/layout.tsx`.

### Coming in branch `fix/chat-page-fetch-timeouts`: bounded proxy timeouts

**Coming in branch `fix/chat-page-fetch-timeouts`** (uncommitted working-tree changes; not on
`preview/view` yet). User-visible effects on Chat:

- **Proxy timeout.** The Next proxy stops waiting for the backend's response headers after
  `BACKEND_TIMEOUT_MS` (default **60 s**) for GET/HEAD/OPTIONS and `BACKEND_WRITE_TIMEOUT_MS`
  (default **180 s**) for requests with a body, which includes `POST /query` and feedback. It then
  answers **504** with **The backend did not respond within N seconds. Please try again.**, which
  Chat shows in red in the answer card instead of endless dots. Streams (SSE) are never cut off once
  headers arrive.
- **Automatic retries for reads only.** GETs (session list, opening a session, branding) and sign-out
  are retried after a connection error or a 502/503/504, up to `BACKEND_RETRIES` (default **2**,
  max 5), with jittered backoff (250-500 ms, then 500-1000 ms). `POST /query`, feedback, rename and
  delete are **never** retried, so a question is never asked twice. A timeout is not retried.
- **Browser disconnect is passed on:** leaving the page aborts the backend call.
- **Session check failure:** if `/auth/me` times out, `/chat` shows **We couldn't check your
  session** / **The service may be unavailable. Try again in a moment.** once, with a retry button,
  and no longer loops on re-navigation or re-polling.
- **Root error page:** a new last-resort screen **Something went wrong** / **This page could not be
  loaded** / **Try again in a moment.** with **Try again** and **Go to the start page**.
- **Branding:** the server-side branding fetch is cached in-process and bounded (2.5 s), and the
  backend's branding DB read is bounded by `BRANDING_LOAD_TIMEOUT_SECONDS` (default 5 s); on timeout
  the default branding (name, sample questions) is used, so a slow backend no longer holds every page
  render open.
- **Dashboard data caches** back off after a failure (1 s, 2 s, 4 s ... up to 60 s) before retrying on
  focus or remount (does not affect Chat itself).
- Backend side: DB pool wait raised to `DB_POOL_TIMEOUT_SECONDS` (default 120 s) and
  `AGG_POOL_RESERVE` (default 5) connections kept free from dashboard aggregation, so chat requests
  and sign-in checks are less likely to starve when dashboards are busy.

Source (branch): `frontend/app/api/proxy/[...path]/route.ts`, `frontend/lib/server/backend.ts`,
`frontend/.env.local.example`, `frontend/app/global-error.tsx`, `frontend/lib/brand-server.ts`,
`frontend/lib/query-cache.ts`, `backend/app/core/config.py`, `backend/app/core/concurrency.py`,
`backend/app/core/database.py`, `frontend/tests/journeys/chat-backend-timeout.test.tsx`.

### Other branches touching Chat

- **Coming in branch `fix/shell-nav-logout-theme-dates`:** session-list ages use the shared
  `RelativeTime` component (relative text with the full local date-time and timezone as a tooltip).
- **Coming in branch `feat/realistic-demo-data`:** the `query` audit event also records the assistant
  `message_id`, and online evaluation receives the answer latency (no visible change on `/chat`).
