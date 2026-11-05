---
url: https://deployiq.co/contact-us
route: /contact-us
title: "Contact Us - Deploy IQ"
description: "Get in touch with Deploy IQ. Collaborate on your new business idea."
source:
  - src/app/(public)/contact-us/page.tsx
  - src/components/pages/ContactUs.tsx
  - src/app/api/contact/route.ts
---

# Contact Us

Collaborate on your new business idea.

## Contact Information

_(Heading is styled in uppercase on screen.)_

### Australia

**Sydney Office**
Suite B5-B6, 12-14 Solent Circuit, Norwest,<br>
NSW, 2153 Australia

**Melbourne Office**
Level 2 Riverside Quay, 1 Southbank Blvd,<br>
Melbourne VIC 3006

**Brisbane Office**
Level 19, 10 Eagle Street, Brisbane QLD<br>
4000 Australia

Call
**[Link]** 1300 394 468 → tel:1300394468

Mail
**[Link]** info@deployiq.co → mailto:info@deployiq.co

## Request We Contact You

_(Heading is styled in uppercase on screen.)_

### [Section: Enquiry category tabs]

- Sales _(selected by default)_
- Support
- Careers
- Others

_(The tabs have no content of their own; the selected tab is sent with the form as the enquiry category. The form below is the same for every tab.)_

### [Section: Contact form]

- **Name** \* (required) — placeholder: "Your name"
- **Email** \* (required) — placeholder: "you@company.com"
- **Phone** — placeholder: "+61 ..."
- **Company** — placeholder: "Company name"
- **Service Interested In** — select, placeholder: "Select a service"
  - Zero Trust Implementation
  - Essential Eight Compliance
  - Security Assessment
  - Managed Security Services
  - Compliance & Governance
  - Other
- **How can we help you?** — textarea, placeholder: "Tell us about your project or question..."

Deploy IQ is committed to protecting the security of your personal data. By submitting your information here you agree to let us use your personal data for our marketing purposes.

**[CTA]** Submit → submits the form

- _(While sending)_ button text: Sending...
- _(After successful submit, for ~2 seconds, then the form resets)_ button text: Message Sent!

## UI messages

Toasts shown after submitting the contact form:

- _(Success)_ Thank you! We'll be in touch soon.
- _(Server error, default)_ Failed to send message. Please try again.
- _(Network/unexpected error)_ Something went wrong. Please try again.

Server-returned error messages that can appear in the error toast (from `src/app/api/contact/route.ts`):

- Invalid JSON body
- Invalid contact request
- Name and email are required
- A valid email address is required
- Invalid name length
- Too many contact requests. Please try again later.
- Failed to send message. Please try again.
