"""
backend/services/email_service.py — Pluggable email delivery service
Ask TechBear — Gymnarctos Studios LLC

Provides an EmailService protocol with swappable implementations
selected via the EMAIL_BACKEND environment variable.

Supported backends:
    logging  — logs the email, no actual send (default / dev / CI)
    smtp     — sends via SMTP (Gmail app password, Postmark, etc.)

Future backends (not yet implemented):
    hubspot  — HubSpot transactional email API
    sendgrid — SendGrid API

Environment variables:
    EMAIL_BACKEND=logging|smtp          (default: logging)

    For smtp backend:
    EMAIL_SMTP_HOST=smtp.gmail.com
    EMAIL_SMTP_PORT=587
    EMAIL_SMTP_USER=therealtechbeardiva@gmail.com
    EMAIL_SMTP_PASSWORD=<app password>  (not account password)
    EMAIL_FROM=techbear@gymnarctosstudiosllc.com
    EMAIL_FROM_NAME=TechBear

Usage:
    from backend.services.email_service import get_email_service

    service = get_email_service()
    sent = await service.send(
        to="attendee@example.com",
        subject="TechBear answered your question!",
        body_markdown="...",
    )

Notes:
    - Body is Markdown. SMTP backend renders a plain-text version for
      delivery; HTML rendering is a future enhancement.
    - attendee_email is PII. Never log the address at INFO level or above.
      The logging backend uses DEBUG only.
    - Delivery is logged as a pipeline_artifact row by the caller
      (pipeline_review.py approve endpoint). The email address is NOT
      stored in the artifact — only the delivery timestamp and status.
"""

import logging
import os
import re
import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# =============================================================
# Protocol — the interface all backends implement
# =============================================================


@runtime_checkable
class EmailService(Protocol):  # pylint: disable=unnecessary-ellipsis
    """
    Minimal email delivery interface.

    All backends implement this protocol. Swap implementations
    via EMAIL_BACKEND env var without touching application code.
    """

    async def send(
        self,
        to: str,
        subject: str,
        body_markdown: str,
    ) -> bool:
        """
        Send an email.

        Args:
            to:            recipient address
            subject:       email subject line
            body_markdown: response body in Markdown format

        Returns:
            True if delivery succeeded or was logged, False on error.
        """
        ...  # pylint: disable=unnecessary-ellipsis


# =============================================================
# Logging backend — default, safe for dev/CI/testing
# =============================================================


class LoggingEmailService:
    """
    Email backend that logs instead of sending.

    Default backend. Safe for development, benchmarking, and CI.
    Switch to SMTPEmailService for real delivery.

    Email addresses are logged at DEBUG only to avoid PII in
    production logs.
    """

    async def send(
        self,
        to: str,
        subject: str,
        body_markdown: str,
    ) -> bool:
        """Log email details without sending. Returns True always."""
        logger.debug("📧 [LoggingEmailService] Would send to: %s", to)
        logger.info(
            "📧 [LoggingEmailService] Email suppressed (EMAIL_BACKEND=logging)\n"
            "  Subject: %s\n"
            "  Body preview: %s",
            subject,
            body_markdown[:120].replace("\n", " "),
        )
        return True


# =============================================================
# SMTP backend — real delivery via Gmail or any SMTP provider
# =============================================================


class SMTPEmailService:
    """
    Email backend that sends via SMTP.

    Reads configuration from environment variables at send time
    (not at import time) so .env changes don't require restart.

    Gmail setup notes:
        - Use an App Password, not your account password.
          Google Account → Security → 2-Step Verification → App passwords.
        - The From address must be configured as a "Send mail as" alias
          in Gmail settings before Google will accept it as a sender.
        - SPF/DKIM DNS records must authorize Gmail for your domain
          (gymnarctosstudiosllc.com) for deliverability.

    For event-day use, switch EMAIL_BACKEND=smtp in .env.
    """

    def _build_plain_text(self, body_markdown: str) -> str:
        """
        Convert Markdown to plain text for email delivery.

        Strips common Markdown syntax. Full HTML rendering
        is a future enhancement.
        """
        text = body_markdown
        # Strip headers
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Strip bold/italic
        text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
        text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
        # Strip inline code
        text = re.sub(r"`(.+?)`", r"\1", text)
        # Wrap at 72 chars for email readability
        paragraphs = text.split("\n\n")
        wrapped = "\n\n".join(
            textwrap.fill(p.strip(), width=72) for p in paragraphs if p.strip()
        )
        return wrapped

    async def send(
        self,
        to: str,
        subject: str,
        body_markdown: str,
    ) -> bool:
        """Send email via SMTP. Returns True on success, False on error."""
        host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        user = os.getenv("EMAIL_SMTP_USER", "")
        password = os.getenv("EMAIL_SMTP_PASSWORD", "")
        from_addr = os.getenv("EMAIL_FROM", user)
        from_name = os.getenv("EMAIL_FROM_NAME", "TechBear")

        if not user or not password:
            logger.error(
                "📧 [SMTPEmailService] EMAIL_SMTP_USER or EMAIL_SMTP_PASSWORD "
                "not set — cannot send. Set EMAIL_BACKEND=logging to suppress."
            )
            return False

        plain_text = self._build_plain_text(body_markdown)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_addr}>"
        msg["To"] = to
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))

        try:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(user, password)
                smtp.sendmail(from_addr, [to], msg.as_string())
            logger.info(
                "📧 [SMTPEmailService] Sent '%s' via %s:%s", subject, host, port
            )
            return True
        except smtplib.SMTPException as exc:
            logger.error("📧 [SMTPEmailService] SMTP error: %s", exc)
            return False
        except OSError as exc:
            logger.error("📧 [SMTPEmailService] Network error: %s", exc)
            return False


# =============================================================
# Factory — returns the configured backend
# =============================================================


def get_email_service() -> EmailService:
    """
    Return the email backend configured by EMAIL_BACKEND env var.

    EMAIL_BACKEND=logging  → LoggingEmailService (default)
    EMAIL_BACKEND=smtp     → SMTPEmailService

    Raises ValueError for unrecognised backend names so
    misconfiguration fails loudly at startup rather than silently
    at send time.
    """
    backend = os.getenv("EMAIL_BACKEND", "logging").lower().strip()

    if backend == "logging":
        return LoggingEmailService()
    if backend == "smtp":
        return SMTPEmailService()

    raise ValueError(
        f"Unknown EMAIL_BACKEND '{backend}'. "
        "Valid options: logging, smtp"
    )
