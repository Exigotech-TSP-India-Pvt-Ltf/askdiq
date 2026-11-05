---
url: https://deployiq.co/get-started
route: /get-started
title: "Get Started | DeployIQ"
description: "Start your security journey with DeployIQ."
source:
  - src/app/(public)/get-started/page.tsx
  - src/components/pages/GetStarted.tsx
---

# Welcome to your Deploy IQ Assessment

Your hub for security insights, compliance readiness, and actionable outcomes-all powered by AI agents and human expertise.

## [Section: Journey progress banner]

Steps (in order):

1. Free Assessment
2. Create Account
3. Know your Security Posture
4. Talk to an Expert
5. Implementation & Reports

_(The first step not yet completed shows the label "(you are here)" beneath it. Completed steps show a tick. Progress comes from the signed-in user's checklist; for visitors with no checklist, progress is 0% and "Free Assessment" is marked "(you are here)".)_

{progress}% complete — e.g. "0% complete"

## [Section: Sidebar]

### Your Journey

Track your security assessment progress

- Security Posture Assessment
- Sign up complete
- Security Posture Dashboard

_(Each item shows a tick when completed.)_

Progress {progress}% — e.g. "Progress 0%"

### What you'll get:

- AI-powered analysis
- Personalized recommendations
- Compliance insights

**[CTA]** Book a human consultant → /booking

## Get Your Free Security Posture Assessment

Discover your security vulnerabilities in just 5 minutes

- _(Shown when the signed-in user has NOT completed an assessment, and for signed-out visitors — `hasAssessment = false`)_ **[CTA]** Start Assessment → /assessment
- _(Shown when the signed-in user HAS completed an assessment — `hasAssessment = true`)_ **[CTA]** Go to Dashboard → /dashboard

### Your Security Score

Quickly view your overall security posture, key vulnerabilities, and risk score at a glance.

Takes only 5 minutes

### Compliance Score

Monitor alignment with the ACSC Essential Eight, with automated updates and guidance.

Real-time analysis

### Actionable Recommendations

Receive AI-prioritized, human-validated steps to reduce exposure and strengthen defenses.

Priority ranked

### Recent Activity & Assessments

Track completed scans, workshop outputs, and implemented policies.

AI-powered support

### Workshops & Consulting

Schedule workshops and consult with DeployIQ human experts directly.

Priority ranked

### Support & Resources

Access guides, get help, or contact our support team for assistance with DeployIQ.

AI-powered support

**[CTA]** Maybe Later → /
**[CTA]** Our Services → /services

## [Section: Quick links]

### Zero Trust

Comprehensive security architecture assessment

**[Link]** Learn more → /zero-trust

### E8 Compliance

Essential Eight compliance management services

**[Link]** Learn more → /essential-eight

### Support

24/7 expert security assistance available

**[Link]** Learn more → /booking
