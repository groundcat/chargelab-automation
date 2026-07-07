"""IMAP verification-code retrieval and SMTP result notifications."""

import email as emaillib
import imaplib
import logging
import os
import re
import smtplib
import time
from email.mime.text import MIMEText

log = logging.getLogger("emailer")

IMAP_HOST = os.environ["LOGIN_EMAIL_HOST"]
IMAP_PORT = int(os.environ.get("LOGIN_EMAIL_SECURE_IMAP_PORT", "993"))
SMTP_HOST = os.environ["LOGIN_EMAIL_HOST"]
SMTP_PORT = int(os.environ.get("LOGIN_EMAIL_SECURE_SMTP_PORT", "587"))
EMAIL_USER = os.environ["LOGIN_EMAIL_USERNAME"]
EMAIL_ADDR = os.environ["LOGIN_EMAIL"]
EMAIL_PASS = os.environ["LOGIN_EMAIL_PASSWORD"]

# Accept 5 or 6 digit codes; the ChargeID app currently sends 5 digits
# ("Your one-time verification code is 29411") but the format may vary.
CODE_RE = re.compile(r"\b(\d{5,6})\b")


def _imap_connect():
    m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    m.login(EMAIL_USER, EMAIL_PASS)
    m.select("INBOX")
    return m


def _msg_text(msg):
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode(errors="replace"))
    return "\n".join(parts)


def get_inbox_baseline():
    """Return the set of message ids currently in the inbox."""
    m = _imap_connect()
    try:
        _, data = m.search(None, "ALL")
        return set(data[0].split())
    finally:
        m.logout()


def wait_for_code(baseline_ids, timeout=180, poll_interval=5):
    """Poll the inbox for a new message containing a 5-6 digit code.

    Only messages that arrived after `baseline_ids` was captured are
    considered, so stale codes from previous runs are never reused.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            m = _imap_connect()
            try:
                _, data = m.search(None, "ALL")
                ids = data[0].split()
                for mid in reversed([i for i in ids if i not in baseline_ids]):
                    _, msg_data = m.fetch(mid, "(RFC822)")
                    msg = emaillib.message_from_bytes(msg_data[0][1])
                    match = CODE_RE.search(_msg_text(msg))
                    if match:
                        log.info("Found code in email from %s (subject: %s)",
                                 msg.get("From"), msg.get("Subject"))
                        return match.group(1)
            finally:
                m.logout()
        except Exception:
            log.exception("IMAP poll failed, retrying")
        time.sleep(poll_interval)
    return None


def send_result(subject, body):
    """Email the runtime result to the user via SMTP (STARTTLS)."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDR
    msg["To"] = EMAIL_ADDR
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_ADDR, [EMAIL_ADDR], msg.as_string())
        log.info("Result email sent: %s", subject)
    except Exception:
        log.exception("Failed to send result email")
