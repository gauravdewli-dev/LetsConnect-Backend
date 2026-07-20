import logging
import re

import httpx

from app.config import get_settings
from app.constants import BREVO_API, EMAIL_INTRO_HTML, EMAIL_INTRO_TEXT, RESEND_API

logger = logging.getLogger(__name__)


def send_otp_email(*, to_email: str, otp: str) -> None:
    _send_email(
        to_email=to_email,
        subject="LetsConnect password reset code",
        heading="Your LetsConnect password reset code is:",
        otp=otp,
        footer="This code expires in 10 minutes. If you did not request this, ignore this email.",
    )


def send_signup_verification_email(*, to_email: str, otp: str) -> None:
    _send_email(
        to_email=to_email,
        subject="Verify your LetsConnect account",
        heading="Your LetsConnect verification code is:",
        otp=otp,
        footer="Enter this code to verify your email and sign in. Expires in 10 minutes.",
    )


def _parse_from_address(from_header: str) -> tuple[str, str]:
    match = re.match(r"^(?:(.+?)\s*)?<([^>]+)>$", from_header.strip())
    if match:
        name = (match.group(1) or "").strip().strip('"')
        return name or "LetsConnect", match.group(2).strip()
    return "LetsConnect", from_header.strip()


def _resolve_provider() -> str | None:
    settings = get_settings()
    choice = settings.email_provider
    if choice == "brevo":
        return "brevo" if settings.brevo_api_key else None
    if choice == "smtp":
        return "smtp" if settings.smtp_host and settings.email_from else None
    if choice == "resend":
        return "resend" if settings.resend_api_key and settings.email_from else None
    # auto: prefer Brevo (sends to any address on free tier after sender verify)
    if settings.brevo_api_key and settings.email_from:
        return "brevo"
    if settings.smtp_host and settings.email_from:
        return "smtp"
    if settings.resend_api_key and settings.email_from:
        return "resend"
    return None


def _send_email(*, to_email: str, subject: str, heading: str, otp: str, footer: str) -> None:
    settings = get_settings()
    html = (
        f"{EMAIL_INTRO_HTML}"
        f"<p>{heading}</p>"
        f"<p style='font-size:24px;font-weight:bold;letter-spacing:4px'>{otp}</p>"
        f"<p>{footer}</p>"
    )
    text = f"{EMAIL_INTRO_TEXT}\n{heading} {otp}\n\n{footer}"
    sender_name, sender_email = _parse_from_address(settings.email_from or "LetsConnect <noreply@example.com>")

    provider = _resolve_provider()
    if provider == "brevo":
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            sender_name=sender_name,
            sender_email=sender_email,
            to_email=to_email,
            subject=subject,
            html=html,
            text=text,
        )
        return
    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            from_email=settings.email_from,
            to_email=to_email,
            subject=subject,
            html=html,
            text=text,
        )
        return
    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            from_email=settings.email_from,
            to_email=to_email,
            subject=subject,
            html=html,
            text=text,
        )
        return

    logger.warning(
        "Email not configured — OTP for %s: %s (set BREVO_API_KEY, SMTP_*, or RESEND_API_KEY in .env)",
        to_email,
        otp,
    )


def _send_via_brevo(
    *,
    api_key: str,
    sender_name: str,
    sender_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            BREVO_API,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            json={
                "sender": {"name": sender_name, "email": sender_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html,
                "textContent": text,
            },
        )
    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or detail
        except Exception:
            pass
        logger.error("Brevo API error (%s): %s", response.status_code, detail)
        raise RuntimeError(f"Failed to send email: {detail}")


def _send_via_resend(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            RESEND_API,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_email,
                "to": [to_email],
                "subject": subject,
                "html": html,
                "text": text,
            },
        )
    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or detail
        except Exception:
            pass
        logger.error("Resend API error (%s): %s", response.status_code, detail)
        raise RuntimeError(f"Failed to send email: {detail}")


def _send_via_smtp(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.sendmail(from_email, [to_email], msg.as_string())
