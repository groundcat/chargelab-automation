"""Headless-browser automation of the ChargeID (EVStart) charging flow."""

import logging
import os
import time

from playwright.sync_api import sync_playwright

import emailer

log = logging.getLogger("charger")

BASE_URL = os.environ["CHARGEID_BASE_URL"].rstrip("/")
CHARGER_ID = os.environ["CHARGEID_CHARGER_ID"]
CHARGER_URL = f"{BASE_URL}/{CHARGER_ID}"
LOGIN_EMAIL = os.environ["CHARGEID_LOGIN_EMAIL"]

STATE_PATH = os.environ.get("BROWSER_STATE_PATH", "/data/browser_state.json")


class ChargeError(Exception):
    pass


def _save_state(context):
    """Persist cookies/localStorage so future runs can skip the login."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        context.storage_state(path=STATE_PATH)
    except Exception:
        log.warning("Could not persist browser state", exc_info=True)


def _page_text(page, limit=1500):
    try:
        return page.evaluate("() => document.body.innerText").strip()[:limit]
    except Exception:
        return "(could not read page text)"


def _login(page):
    """Perform the passwordless email-code login."""
    log.info("Logging in as %s", LOGIN_EMAIL)
    baseline = emailer.get_inbox_baseline()

    page.get_by_role("button", name="Log in").click()
    page.get_by_placeholder("Email address or mobile phone number").fill(
        LOGIN_EMAIL, timeout=15000)
    page.get_by_role("button", name="Next").click()

    # Code entry field appears once the email has been requested.
    page.get_by_placeholder("Enter code").wait_for(timeout=20000)

    log.info("Verification code requested, polling inbox...")
    code = emailer.wait_for_code(baseline, timeout=180)
    if not code:
        raise ChargeError("Timed out waiting for the verification code email.")
    log.info("Got verification code %s", code)

    page.get_by_placeholder("Enter code").fill(code)
    page.get_by_role("button", name="Sign in").click()

    # Wait for the code-entry screen to go away, whatever page follows —
    # the app sometimes lands on a recent-session summary instead of the
    # charger page, so don't require the Start button here.
    try:
        page.get_by_placeholder("Enter code").wait_for(state="detached",
                                                       timeout=30000)
    except Exception:
        raise ChargeError(
            "Sign-in did not complete (code may have been rejected). "
            "Page content:\n" + _page_text(page))
    page.wait_for_timeout(3000)
    log.info("Login successful")


def start_charging_session():
    """Run the full flow. Returns (subject_suffix, summary) strings.

    Raises ChargeError with page context on failure.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_args = {}
        if os.path.exists(STATE_PATH):
            context_args["storage_state"] = STATE_PATH
            log.info("Loaded saved login state from %s", STATE_PATH)
        context = browser.new_context(**context_args)
        page = context.new_page()
        try:
            log.info("Opening %s", CHARGER_URL)
            page.goto(CHARGER_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            # A saved session may still be valid; log in only if needed.
            if page.get_by_role("button", name="Log in").count():
                log.info("No valid saved session, performing email-code login")
                _login(page)
                # Login can land on an old session summary; reload the
                # charger page to get a clean state.
                page.goto(CHARGER_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
            else:
                log.info("Already logged in, reusing saved session")
            # Save state on every run (not just after a fresh login):
            # Cognito refreshes tokens silently, and persisting the
            # refreshed ones keeps the saved session valid indefinitely.
            _save_state(context)

            body = _page_text(page)

            # "Time to move your vehicle" = the previous session finished and
            # the car is fully charged but still plugged in. Nothing to start.
            if "Time to move your" in body:
                log.info("Charging already completed for the plugged-in vehicle")
                return ("Charging already complete",
                        "Charging is already complete — the vehicle is fully "
                        "charged and still plugged in. No new session was "
                        "started.\n\nCharger status:\n" + body)

            if page.get_by_role("button", name="Stop").count():
                log.info("A charging session is already active")
                return ("Session already active",
                        "A charging session is already active on this "
                        "charger — no new session was started.\n\n"
                        "Charger status:\n" + body)

            start = page.get_by_role("button", name="Start session")
            if not start.count():
                raise ChargeError(
                    "Start session button not found. Page content:\n" + body)
            start.click()
            log.info("Clicked Start session, waiting for session to begin...")

            # The site shows a "plug in" countdown (~90s), then redirects to
            # ?sessionId=... once the charger recognizes the vehicle.
            deadline = time.time() + 150
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                if "sessionId=" in page.url:
                    body = _page_text(page)
                    log.info("Session started: %s", page.url)
                    return ("Charging session started",
                            "Charging session started successfully.\n\n"
                            f"Session URL: {page.url}\n\n"
                            f"Charger status:\n{body}")
                body = _page_text(page, 400)
                if "plugged in" not in body and "plug in" not in body.lower():
                    # Countdown screen gone without a session -> something failed
                    break

            raise ChargeError(
                "Session did not start (vehicle may not be plugged in, or the "
                "plug-in window expired). Last page content:\n"
                + _page_text(page))
        finally:
            try:
                context.close()
            finally:
                browser.close()
