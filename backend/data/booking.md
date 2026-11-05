---
url: https://deployiq.co/booking
route: /booking
title: "Book a Consultation - Deploy IQ"
description: "Schedule a session with our security experts to discuss your needs."
source:
  - src/app/(public)/booking/page.tsx
  - src/components/pages/Booking.tsx
---

# Book your session

Speak directly with a Deploy IQ security and compliance expert to begin your resilience journey.

## [Section: Booking card — consultant info (left)]

Talk with
Principal Security Consultation

![Deploy IQ](/logo-symbol.svg)

### Session Details

- Kick-off & Discovery
- 30 minutes
- Web conference details to follow

### Timezone

- AUS Eastern Standard Time

## [Section: Booking card — calendar (right)]

### Select a Date & Time

_(Month navigation shows the current month and year, e.g. "November 2026", with previous/next arrow buttons.)_

Calendar day headings:

- Sun
- Mon
- Tue
- Wed
- Thu
- Fri
- Sat

_(Day numbers for the month are shown below; past dates are greyed out and cannot be selected. Today's date is selected by default.)_

### [Section: Time slots]

- _(When a date is selected)_ Selected Date: {Month} {day} — e.g. "Selected Date: November 4"
- _(When no date is selected, e.g. after changing month)_ Select a date

- _(While loading)_ Checking availability...
- _(When slots exist)_ a grid of available start times, e.g. "10:30 AM" _(times are loaded from the calendar, not fixed copy)_
- _(When no slots exist)_ No slots available.

**[CTA]** confirm button:

- _(No time selected — button disabled)_ Select a time
- _(Time selected)_ Confirm Booking for {time} — e.g. "Confirm Booking for 10:30 AM"
- _(While booking)_ Booking...

_(If the visitor is not signed in, clicking the confirm button redirects to /signin.)_

## Booking Confirmed!

_(After successful booking — replaces the whole page content.)_

Your session has been successfully scheduled. We have sent a calendar invitation to {signed-in user's email}.

**[CTA]** Go to Dashboard → /dashboard

## UI messages

- _(Browser alert, booking failed)_ Failed to create booking. Please try again.
