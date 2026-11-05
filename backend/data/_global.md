---
scope: global
source:
  - src/app/(public)/layout.tsx
  - src/components/layout/MainLayout.tsx
  - src/components/layout/Navbar.tsx
  - src/lib/service-pages.ts
  - src/components/layout/Footer.tsx
  - src/components/modals/CookieNoticeModal.tsx
  - src/components/modals/WebsiteDisclaimerModal.tsx
  - src/components/layout/Chatbot.tsx
  - src/lib/chatbot.ts
---

# Site-wide copy

_(Rendered on every public page via `MainLayout`: Navbar, Footer (which mounts the Cookie notice and Website disclaimer modals), and the Chatbot widget.)_

## Navbar

![DeployIQ Logo](/logo.svg) → /

### [Section: Desktop navigation]

- **[Link]** Platform → /platform
- **[Link]** Services → /services _(hover/focus opens dropdown)_
  - Zero Trust Services → /zero-trust
  - Essential Eight Services → /essential-eight
  - Microsoft 365 Security Baseline → /microsoft-365-security-baseline-assessment
  - ISO 27001 Technical Controls → /iso-27001-microsoft-365-technical-controls
  - CIS Microsoft 365 Benchmark → /cis-microsoft-365-foundations-benchmark-assessment
  - ISO 42001 Technical Controls → /iso-42001-ai-management-system
- Trust Center _(dropdown button, not a link)_
  - Responsible AI → /responsible-ai
  - Secure AI → /secure-ai
- **[Link]** About Us → /about-us

### [Section: Account buttons]

_(Shown when signed out)_

- **[CTA]** Sign In → /signin
- **[CTA]** Get Started → /get-started

_(Shown when signed in)_

- **[Link]** Dashboard → /dashboard
- **[CTA]** Logout → signs out and returns to /

### [Section: Mobile menu]

_(Opened by the menu icon button; same links as desktop, with group labels.)_

- **[Link]** Platform → /platform
- **[Link]** Services → /services _(group label, shown in uppercase)_
  - Zero Trust Services → /zero-trust
  - Essential Eight Services → /essential-eight
  - Microsoft 365 Security Baseline → /microsoft-365-security-baseline-assessment
  - ISO 27001 Technical Controls → /iso-27001-microsoft-365-technical-controls
  - CIS Microsoft 365 Benchmark → /cis-microsoft-365-foundations-benchmark-assessment
  - ISO 42001 Technical Controls → /iso-42001-ai-management-system
- Trust Center _(group label, shown in uppercase, not a link)_
  - Responsible AI → /responsible-ai
  - Secure AI → /secure-ai
- **[Link]** About Us → /about-us

_(Shown when signed out)_

- **[CTA]** Sign In → /signin
- **[CTA]** Get Started → /get-started

_(Shown when signed in)_

- **[Link]** Dashboard → /dashboard
- **[CTA]** Logout → signs out and returns to /

## Footer

![DeployIQ](/logo-light.svg) → /

DeployIQ turns cybersecurity frameworks into operable, verifiable outcomes, helping organisations understand, reduce, and govern cyber risk through intelligent, human-guided solutions.

### [Column: Platform links]

_(No visible column heading.)_

- Deploy IQ Platform → /platform
- Services → /services
- Zero Trust Services → /zero-trust
- Essential Eight Services → /essential-eight
- Microsoft 365 Security Baseline → /microsoft-365-security-baseline-assessment
- ISO 27001 Technical Controls → /iso-27001-microsoft-365-technical-controls
- CIS Microsoft 365 Benchmark → /cis-microsoft-365-foundations-benchmark-assessment
- ISO 42001 Technical Controls → /iso-42001-ai-management-system

### [Column: About links]

_(No visible column heading.)_

- About Us → /about-us
- Contact Us → /contact-us
- Our Vision → /our-vision

### [Column: Support links]

_(No visible column heading.)_

- Responsible AI Commitments → /responsible-ai
- Privacy → /privacy
- Terms → /terms

### [Section: Bottom bar]

© Deploy IQ 2026. All rights reserved.

- **[CTA]** Cookie Notice → opens modal "Cookie Notice"
- **[CTA]** Website Disclaimer → opens modal "Website Disclaimer"

## Cookie notice modal

_(Opens automatically about 1 second after page load if the visitor has not yet chosen "Essential Only" or "Accept All"; also opens from the footer "Cookie Notice" link.)_

### Cookie Notice

Understanding our cookie usage and your privacy choices

Deploy IQ uses cookies and similar technologies to support essential website functionality and measure engagement.

### 1 Types of Cookies

**Strictly Necessary**
Support security, navigation, and baseline operation.

**Performance & Analytics**
Provide aggregated insights into site usage patterns.

**Functional**
Enable optional features such as form memory or chat state.

**Third Party**
Set by providers that support analytics or embedded components.

### 2 Managing Cookies

Users may block or remove cookies through browser settings. Some features may be impacted.

### 3 Contact

For questions about our cookie policy:
**[Link]** info@deployiq.co → mailto:info@deployiq.co

### [Section: Modal footer]

Last updated: January 2026

- **[CTA]** Essential Only → saves "necessary" cookie choice and closes
- **[CTA]** Accept All → saves "all" cookie choice and closes

## Website disclaimer modal

_(Opens from the footer "Website Disclaimer" link.)_

### Website Disclaimer

The information on this website is provided for general informational purposes. **Deploy IQ** does not guarantee accuracy, completeness, or applicability to specific situations.

**Nothing on this site constitutes:**

- Legal advice
- Security certification
- Compliance determination
- A substitute for professional judgement

External links are for convenience only. **Deploy IQ** is not responsible for thirdparty content or practices.

## Chatbot widget

_(Floating round chat-icon button, bottom-right of every page. It has no visible text; its screen-reader label is "Open Deploy IQ chat".)_

### Deploy IQ Assistant

_(Chat panel header, shown when the chat is open.)_

Your security questions, answered

### [Section: Chat toolbar]

- **[CTA]** Previous chats → shows the chat history list _(when the history list is open this button reads: Back to chat)_
- **[CTA]** New chat → starts a new conversation

### [Section: Conversation view]

_(Title of the current conversation is shown above the messages; defaults to "New chat".)_

Welcome message (always shown first):

> Hello! I’m the Deploy IQ Assistant. Ask me about Deploy IQ, Zero Trust, Essential Eight, Responsible AI, or our security services.

- _(While waiting for a reply)_ Thinking…
- _(While opening a previous chat)_ Loading chat…

Message box placeholder: Ask a question…

_(When the session has expired)_

This session has expired after 60 minutes of inactivity.

**[CTA]** Start a new chat

Footnote (always shown under the message box):

Sessions expire after 60 minutes of inactivity.

### Previous chats

_(History list view.)_

- _(Loading)_ Loading chats…
- _(Empty)_ Your conversations will appear here.
- Each chat shows its title (or "Untitled chat") and a status line: "Active" or "Expired · read only", followed by " · {n} messages" — e.g. "Active · 4 messages"
- _(Delete confirmation, after clicking the bin icon)_ Delete this conversation permanently?
  - **[CTA]** Delete chat
  - **[CTA]** Cancel

## UI messages

Chatbot notices and errors:

- Showing messages saved in this browser.
- Past messages are unavailable until the message-history API is configured.
- Showing messages saved in this browser. Server history could not be loaded.
- Past messages could not be loaded.
- Unable to load chat history.
- Unable to load chats.
- Unable to read the chat list.
- Unable to start a new chat.
- The chat service did not return a session ID.
- Unable to send your message.
- The chat service returned an incomplete response.
- Unable to delete this chat.
- Chat is temporarily unavailable. Please try again.
- Chat is unavailable. Please try again.
- Unable to read this chat’s message history.
- The message-history response has an unsupported format.
