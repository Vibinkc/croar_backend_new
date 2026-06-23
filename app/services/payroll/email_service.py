"""Transactional email via SMTP (Google/Gmail).

Uses the Python standard library (``smtplib`` + ``email.message``) — no third-
party SDK. ``smtplib`` is blocking, so callers in async request handlers must
invoke :func:`send_payslip_email` through a threadpool (e.g.
``fastapi.concurrency.run_in_threadpool``) to avoid blocking the event loop.

Credentials come from the environment via ``Settings`` — set ``SMTP_USERNAME``,
``SMTP_PASSWORD`` (a Google App Password) and ``SMTP_FROM_EMAIL`` in ``.env``
(see ``.env.sample``). When unset, :class:`EmailNotConfiguredError` is raised so
the API can return a clear 503 instead of a cryptic connection error.

Gmail notes:
- ``SMTP_PASSWORD`` must be a 16-char **App Password** (requires 2-Step
  Verification on the account); the normal account password will not work.
- Gmail forces the From header to the authenticated user, so ``SMTP_FROM_EMAIL``
  should match ``SMTP_USERNAME`` (unless a verified "send mail as" alias exists).
"""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from xml.sax.saxutils import escape  # nosec B406 - escape() sanitizes output strings; it does not parse XML

from app.payroll.settings import Settings

_settings = Settings()


class EmailNotConfiguredError(RuntimeError):
    """Raised when an email send is attempted without SMTP credentials set."""


def is_configured() -> bool:
    """Return True when SMTP host + credentials + sender address are available."""
    return bool(
        _settings.smtp_host
        and _settings.smtp_username
        and _settings.smtp_password
        and _settings.smtp_from_email
    )


def _from_address() -> str:
    name = _settings.smtp_from_name.strip()
    email = _settings.smtp_from_email.strip()
    return f"{name} <{email}>" if name else email


def payslip_email_html(*, employee_name: str, company_name: str, period: str, net_pay: str) -> str:
    """Minimal, inline-styled HTML body for the payslip email."""
    name = escape(employee_name or "there")
    company = escape(company_name)
    period_s = escape(period)
    net = escape(net_pay)
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;max-width:560px;margin:0 auto">
  <h2 style="margin:0 0 4px">{company}</h2>
  <p style="color:#6b7280;margin:0 0 20px">Payslip</p>
  <p>Hi {name},</p>
  <p>Your payslip for <strong>{period_s}</strong> is ready. The full breakdown is
  attached as a PDF.</p>
  <p style="font-size:18px"><strong>Net Payable: {net}</strong></p>
  <p style="color:#6b7280;font-size:13px;margin-top:24px">
    This is an automated message from {company}. If you have questions about your
    pay, please contact your HR/payroll team.
  </p>
</div>"""


def _build_message(
    *, to_email: str, subject: str, html: str, pdf_bytes: bytes, filename: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from_address()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Your payslip is attached as a PDF.")  # plaintext fallback
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)
    return msg


def send_payslip_email(
    *, to_email: str, subject: str, html: str, pdf_bytes: bytes, filename: str
) -> dict[str, Any]:
    """Send a payslip email with the PDF attached via SMTP.

    Raises:
        EmailNotConfiguredError: when SMTP credentials are not configured.
        Exception: any error surfaced by smtplib (connection/auth/send failure).

    """
    if not is_configured():
        raise EmailNotConfiguredError(
            "Email is not configured. Set SMTP_USERNAME, SMTP_PASSWORD and "
            "SMTP_FROM_EMAIL in the environment."
        )

    msg = _build_message(
        to_email=to_email, subject=subject, html=html, pdf_bytes=pdf_bytes, filename=filename
    )
    context = ssl.create_default_context()
    # Enforce a strong minimum TLS version (clears Sonar python:S4423).
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    if _settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(_settings.smtp_host, _settings.smtp_port, context=context) as server:
            server.login(_settings.smtp_username, _settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(_settings.smtp_host, _settings.smtp_port) as server:
            server.starttls(context=context)
            server.login(_settings.smtp_username, _settings.smtp_password)
            server.send_message(msg)

    return {"sent": True, "to": to_email}
