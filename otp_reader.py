"""
otp_reader.py — Reads Naukri OTP from Gmail
Waits for OTP email that arrives AFTER a given timestamp.
Handles 6-box OTP input correctly.
"""

import imaplib
import email
import email.utils
import re
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def fetch_naukri_otp(gmail_address: str, gmail_app_password: str,
                     max_wait_seconds: int = 120,
                     login_triggered_at: datetime = None) -> str:
    """
    Waits for a NEW OTP email that arrives AFTER login_triggered_at.
    Polls every 5 seconds for up to 120 seconds.
    """
    if login_triggered_at is None:
        login_triggered_at = datetime.now(timezone.utc)

    logger.info(f"📧 Waiting for NEW OTP email after {login_triggered_at.strftime('%H:%M:%S')} UTC...")
    logger.info(f"   (ignoring any emails that existed before login)")

    elapsed = 0
    while elapsed < max_wait_seconds:
        try:
            otp = _get_otp_after_time(gmail_address, gmail_app_password, login_triggered_at)
            if otp:
                logger.info(f"✅ Fresh OTP found: {otp}")
                return otp
        except Exception as e:
            logger.warning(f"   Gmail check error: {e}")

        time.sleep(5)
        elapsed += 5
        logger.info(f"   Waiting... ({elapsed}s / {max_wait_seconds}s)")

    logger.error("❌ OTP email did not arrive within timeout.")
    return ""


def _get_otp_after_time(gmail_address: str, app_password: str,
                         after_time: datetime) -> str:
    """
    Search Gmail for OTP emails that arrived strictly AFTER after_time.
    """
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_address, app_password)
    mail.select("inbox")

    today = datetime.now(timezone.utc).strftime("%d-%b-%Y")
    queries = [
        f'(FROM "naukri" SINCE "{today}")',
        f'(SUBJECT "OTP" SINCE "{today}")',
        f'(FROM "naukri")',
    ]

    all_ids = []
    for q in queries:
        try:
            _, result = mail.search(None, q)
            if result and result[0]:
                ids = result[0].split()
                if ids:
                    all_ids = ids
                    break
        except Exception:
            continue

    mail.logout()

    if not all_ids:
        return ""

    # Check last 10 emails newest first
    for msg_id in reversed(all_ids[-10:]):
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(gmail_address, app_password)
            mail.select("inbox")
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            mail.logout()

            if not msg_data or not msg_data[0]:
                continue

            msg      = email.message_from_bytes(msg_data[0][1])
            subject  = msg.get("Subject", "")
            date_str = msg.get("Date", "")
            email_dt = _parse_date(date_str)

            if email_dt is None:
                continue

            diff_seconds = (email_dt - after_time).total_seconds()
            logger.info(f"   📨 '{subject}' | arrived {diff_seconds:+.0f}s vs login time")

            # Only accept emails that arrived AFTER login was triggered
            # Allow 10s buffer for clock differences
            if diff_seconds < -10:
                logger.info(f"   ⏭️  Pre-login email — skipping")
                continue

            body = _get_body(msg)
            otp  = _extract_otp(body)

            if otp:
                logger.info(f"   ✅ Valid OTP: {otp} (arrived {diff_seconds:+.0f}s after login)")
                return otp

        except Exception as e:
            logger.warning(f"   Error: {e}")
            continue

    return ""


def _extract_otp(body: str) -> str:
    """Extract OTP from email body."""
    # Remove HTML tags first
    body_clean = re.sub(r'<[^>]+>', ' ', body)

    patterns = [
        r'OTP[:\s\-]+(\d{4,8})',
        r'code[:\s\-]+(\d{4,8})',
        r'(?<!\d)(\d{6})(?!\d)',
        r'(?<!\d)(\d{4})(?!\d)',
    ]
    for pattern in patterns:
        match = re.search(pattern, body_clean, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _parse_date(date_str: str):
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _get_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    body += str(part.get_payload())
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except Exception:
            body = str(msg.get_payload())
    return body
