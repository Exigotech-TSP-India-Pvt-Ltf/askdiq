"""Alert emails: branded multipart (HTML + plain text) and SMTP delivery.

The HTML is table-based with inline CSS only (renders in Outlook and Gmail).
Every dynamic value is HTML-escaped; details were PII-masked before they were
stored (``app.alerts.service``) and free text is masked again here. No
secrets are ever part of an alert. Delivery uses the standard library
(``smtplib`` in a worker thread), STARTTLS or implicit TLS.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.alerts.catalogue import Severity, rule_for
from app.alerts.service import EVENT_ESCALATED, EVENT_RESOLVED
from app.core.config import Settings
from app.governance.pii import redact_pii
from app.models.db_models import Alert

SEVERITY_COLORS: dict[str, str] = {
    Severity.CRITICAL.value: "#B42318",
    Severity.WARNING.value: "#B54708",
    Severity.INFO.value: "#175CD3",
}
RESOLVED_COLOR = "#067647"
_DEFAULT_PRIMARY = "#1D4ED8"
_MAX_LIST = 10
_MAX_SUBJECT = 200
_FONT = "font-family:Segoe UI,Helvetica,Arial,sans-serif"


@dataclass(frozen=True, slots=True)
class Brand:
    """The branding fields an email needs (from ``app.branding.service``)."""

    product_name: str
    assistant_name: str
    company_name: str
    primary_hex: str = _DEFAULT_PRIMARY
    support_email: str = ""


@dataclass(frozen=True, slots=True)
class AlertView:
    """Plain snapshot of an alert row, so rendering is pure and testable."""

    id: str
    rule: str
    severity: str
    status: str
    title: str
    summary: str
    component: str
    details: dict[str, Any]
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    # UTC ISO timestamp -> local wall time text ("2026-10-19 16:04 AEDT"),
    # resolved by Postgres when the OS has no IANA tz database (Windows).
    local_times: dict[str, str] = field(default_factory=dict)

    def moments(self) -> list[datetime]:
        return [
            m for m in (self.first_seen_at, self.last_seen_at, self.resolved_at) if m
        ]

    @classmethod
    def from_row(cls, alert: Alert) -> AlertView:
        return cls(
            id=str(alert.id),
            rule=alert.rule,
            severity=alert.severity,
            status=alert.status,
            title=alert.title,
            summary=alert.summary or "",
            component=alert.component,
            details=dict(alert.details or {}),
            occurrences=int(alert.occurrences or 1),
            first_seen_at=alert.first_seen_at,
            last_seen_at=alert.last_seen_at,
            resolved_at=alert.resolved_at,
        )


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str
    facts: list[tuple[str, str]] = field(default_factory=list)


def _clean(value: Any) -> str:
    """One-line, PII-masked text (no CR/LF: header and layout safe)."""
    text = " ".join(str(value if value is not None else "").split())
    return redact_pii(text)[0]


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def utc_key(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def format_when(
    moment: datetime | None,
    tz_name: str,
    local_times: dict[str, str] | None = None,
) -> str:
    """'2026-10-19 05:04 UTC (2026-10-19 16:04 AEDT, Australia/Sydney)'."""
    if moment is None:
        return "-"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    utc = moment.astimezone(UTC)
    out = f"{utc:%Y-%m-%d %H:%M} UTC"
    local = (local_times or {}).get(utc_key(utc))
    if local is None:
        zone = _zone(tz_name)
        if zone is not None:
            local = f"{utc.astimezone(zone):%Y-%m-%d %H:%M %Z}"
    if local:
        out += f" ({local}, {tz_name})"
    return out


def _value_text(details: dict[str, Any], key: str) -> str | None:
    value = details.get(key)
    if value is None:
        return None
    unit = details.get("unit")
    if unit == "%":
        return f"{value}%"
    if unit:
        return f"{value} {unit}"
    return str(value)


def _window_text(details: dict[str, Any]) -> str | None:
    if details.get("window"):
        return str(details["window"])
    minutes = details.get("window_minutes")
    return f"last {minutes} minutes" if minutes else None


def _counts_text(details: dict[str, Any]) -> str | None:
    counts = details.get("counts")
    if not isinstance(counts, dict) or not counts:
        return None
    items = list(counts.items())[:_MAX_LIST]
    return ", ".join(f"{str(k).replace('_', ' ')}: {v}" for k, v in items)


def _list_text(values: Any) -> str | None:
    if not isinstance(values, list) or not values:
        return None
    return ", ".join(str(v) for v in values[:_MAX_LIST])


def subject_for(view: AlertView, brand: Brand, event: str) -> str:
    tag = "RESOLVED" if event == EVENT_RESOLVED else view.severity.upper()
    prefix = "Escalated: " if event == EVENT_ESCALATED else ""
    subject = f"[{_clean(brand.assistant_name)}][{tag}] {prefix}{_clean(view.title)}"
    return subject[:_MAX_SUBJECT]


def console_links(view: AlertView, cfg: Settings) -> tuple[str, str]:
    """(page for this alert's rule, alert list) on APP_BASE_URL."""
    base = cfg.app_base_url.rstrip("/")
    rule = rule_for(view.rule)
    page = f"{base}{rule.console_path}" if rule else f"{base}/admin/governance"
    return page, f"{base}/admin/governance"


def build_facts(view: AlertView, cfg: Settings, event: str) -> list[tuple[str, str]]:
    """Label/value rows shared by the HTML and plain-text bodies."""
    d = view.details
    status = "Resolved" if event == EVENT_RESOLVED else view.status.capitalize()
    rows: list[tuple[str, str | None]] = [
        ("Severity", view.severity.upper()),
        ("Status", status),
        ("Component", view.component),
        ("Rule", (rule_for(view.rule).name if rule_for(view.rule) else view.rule)),
        (
            "Detected",
            format_when(view.first_seen_at, cfg.alert_timezone, view.local_times),
        ),
        (
            "Last seen",
            format_when(view.last_seen_at, cfg.alert_timezone, view.local_times),
        ),
        (
            "Resolved",
            format_when(view.resolved_at, cfg.alert_timezone, view.local_times)
            if event == EVENT_RESOLVED
            else None,
        ),
        ("Current value", _value_text(d, "value")),
        ("Threshold", _value_text(d, "threshold")),
        ("Window", _window_text(d)),
        ("Related counts", _counts_text(d)),
        ("Occurrences", str(view.occurrences) if view.occurrences > 1 else None),
        ("Job type", d.get("job_type")),
        ("Last error", d.get("last_error")),
        ("Request IDs", _list_text(d.get("request_ids"))),
        ("Job IDs", _list_text(d.get("job_ids"))),
        ("Alert ID", view.id),
    ]
    return [(label, _clean(value)) for label, value in rows if value not in (None, "")]


def _plain_text(
    view: AlertView,
    brand: Brand,
    facts: list[tuple[str, str]],
    links: tuple[str, str],
    event: str,
) -> str:
    rule = rule_for(view.rule)
    heading = "Resolved" if event == EVENT_RESOLVED else "What happened"
    lines = [
        f"{_clean(brand.product_name)} alert: {_clean(view.title)}",
        "",
        f"{heading}: {_clean(view.summary)}",
        "",
    ]
    width = max(len(label) for label, _ in facts)
    lines += [f"{label.ljust(width)}  {value}" for label, value in facts]
    lines += ["", f"Open in the admin console: {links[0]}", f"All alerts: {links[1]}"]
    if rule:
        lines += ["", f"Runbook: {rule.runbook}"]
    lines += [
        "",
        f"Sent by {_clean(brand.product_name)} ({_clean(brand.company_name)}) "
        "to the ALERT_EMAIL_TO recipients.",
    ]
    return "\n".join(lines) + "\n"


def _html(
    view: AlertView,
    brand: Brand,
    facts: list[tuple[str, str]],
    links: tuple[str, str],
    event: str,
) -> str:
    rule = rule_for(view.rule)
    resolved = event == EVENT_RESOLVED
    badge_color = (
        RESOLVED_COLOR if resolved else SEVERITY_COLORS.get(view.severity, "#475467")
    )
    badge = "RESOLVED" if resolved else view.severity.upper()
    primary = escape(brand.primary_hex or _DEFAULT_PRIMARY)
    product = escape(_clean(brand.product_name))
    heading = "Resolved" if resolved else "What happened"
    rows = "".join(
        "<tr>"
        f'<td style="padding:8px 12px;border-bottom:1px solid #EAECF0;color:#475467;'
        f'{_FONT};font-size:13px;width:150px;vertical-align:top;">{escape(label)}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #EAECF0;color:#101828;'
        f'{_FONT};font-size:13px;vertical-align:top;word-break:break-word;">'
        f"{escape(value)}</td>"
        "</tr>"
        for label, value in facts
    )
    runbook = (
        '<tr><td style="padding:16px 24px 0 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F9FAFB;border:1px solid #EAECF0;border-radius:6px;">'
        f'<tr><td style="padding:12px 16px;{_FONT};font-size:13px;color:#344054;">'
        f"<strong>Runbook:</strong> {escape(rule.runbook)}</td></tr></table>"
        "</td></tr>"
        if rule
        else ""
    )
    page, all_alerts = (escape(link, quote=True) for link in links)
    preheader = escape(_clean(view.summary))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(_clean(view.title))}</title></head>
<body style="margin:0;padding:0;background:#F2F4F7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;">
<tr><td style="background:{primary};padding:16px 24px;border-radius:8px 8px 0 0;{_FONT};font-size:16px;font-weight:bold;color:#FFFFFF;">{product} alert</td></tr>
<tr><td style="padding:24px 24px 0 24px;">
<span style="display:inline-block;background:{badge_color};color:#FFFFFF;{_FONT};font-size:12px;font-weight:bold;letter-spacing:0.5px;padding:4px 10px;border-radius:12px;">{escape(badge)}</span>
<h1 style="margin:12px 0 0 0;{_FONT};font-size:20px;line-height:28px;color:#101828;">{escape(_clean(view.title))}</h1>
</td></tr>
<tr><td style="padding:12px 24px 0 24px;{_FONT};font-size:14px;line-height:22px;color:#344054;"><strong>{heading}:</strong> {escape(_clean(view.summary))}</td></tr>
<tr><td style="padding:16px 24px 0 24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #EAECF0;border-radius:6px;">{rows}</table>
</td></tr>
{runbook}
<tr><td style="padding:24px 24px 8px 24px;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="background:{primary};border-radius:6px;"><a href="{page}" style="display:inline-block;padding:10px 18px;{_FONT};font-size:14px;font-weight:bold;color:#FFFFFF;text-decoration:none;">Open in admin console</a></td>
<td style="width:12px;"></td>
<td style="border:1px solid #D0D5DD;border-radius:6px;"><a href="{all_alerts}" style="display:inline-block;padding:9px 16px;{_FONT};font-size:14px;color:#344054;text-decoration:none;">View governance</a></td>
</tr></table>
</td></tr>
<tr><td style="padding:16px 24px 24px 24px;{_FONT};font-size:12px;line-height:18px;color:#667085;">Sent by {product} ({escape(_clean(brand.company_name))}) to the ALERT_EMAIL_TO recipients. Times are UTC with the local time in brackets.</td></tr>
</table>
</td></tr></table>
</body></html>
"""


def render_alert_email(
    view: AlertView, brand: Brand, cfg: Settings, event: str
) -> RenderedEmail:
    facts = build_facts(view, cfg, event)
    links = console_links(view, cfg)
    return RenderedEmail(
        subject=subject_for(view, brand, event),
        text=_plain_text(view, brand, facts, links, event),
        html=_html(view, brand, facts, links, event),
        facts=facts,
    )


def build_message(rendered: RenderedEmail, cfg: Settings) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = rendered.subject
    message["From"] = cfg.alert_email_from
    message["To"] = ", ".join(cfg.alert_email_recipients)
    message["Date"] = formatdate(localtime=False, usegmt=True)
    sender_domain = cfg.alert_email_from.rsplit("@", 1)[-1].strip(" >") or None
    message["Message-ID"] = make_msgid(domain=sender_domain)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    return message


def _send_sync(message: EmailMessage, cfg: Settings) -> None:
    context = ssl.create_default_context()
    smtp: smtplib.SMTP
    if cfg.smtp_ssl:
        smtp = smtplib.SMTP_SSL(
            cfg.smtp_host,
            cfg.smtp_port,
            timeout=cfg.smtp_timeout_seconds,
            context=context,
        )
    else:
        smtp = smtplib.SMTP(
            cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_seconds
        )
    with smtp:
        if not cfg.smtp_ssl and cfg.smtp_starttls:
            smtp.starttls(context=context)
        if cfg.smtp_username:
            password = cfg.smtp_password.get_secret_value() if cfg.smtp_password else ""
            smtp.login(cfg.smtp_username, password)
        smtp.send_message(message)


async def send_email(message: EmailMessage, cfg: Settings) -> None:
    """Send through SMTP in a worker thread (raises on failure)."""
    await asyncio.to_thread(_send_sync, message, cfg)


def describe_error(exc: BaseException) -> str:
    """Short, secret-free description of a delivery failure for the alert row."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP authentication failed (code {exc.smtp_code})"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"SMTP refused {len(exc.recipients)} recipient(s)"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"SMTP error {exc.smtp_code}"
    return f"{type(exc).__name__}: {_clean(exc)[:200]}"
