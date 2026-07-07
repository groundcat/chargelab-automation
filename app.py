"""Webhook server: GET with the correct secret triggers the charging flow."""

import hmac
import logging
import os
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webhook")

from flask import Flask, jsonify, request  # noqa: E402

import charger  # noqa: E402
import emailer  # noqa: E402

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "80"))
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "UTC"))

app = Flask(__name__)

# Only one charging flow at a time; repeated webhook calls while a run is in
# progress are rejected instead of piling up browser sessions.
_run_lock = threading.Lock()


def _now():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _run_flow():
    started = _now()
    try:
        outcome, result = charger.start_charging_session()
        emailer.send_result(
            f"[ChargeID] {outcome}",
            f"Triggered: {started}\nCompleted: {_now()}\n\n{result}")
        log.info("Flow completed successfully")
    except Exception as exc:
        log.exception("Flow failed")
        emailer.send_result(
            "[ChargeID] FAILED to start charging session",
            f"Triggered: {started}\nFailed: {_now()}\n\n"
            f"Error: {exc}\n\nTraceback:\n{traceback.format_exc()}")
    finally:
        _run_lock.release()


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/", methods=["GET"])
@app.route("/webhook", methods=["GET"])
def webhook():
    secret = request.args.get("secret", "")
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        log.warning("Rejected request with bad secret from %s",
                    request.remote_addr)
        return jsonify(error="unauthorized"), 401

    if not _run_lock.acquire(blocking=False):
        return jsonify(status="busy",
                       message="A charging flow is already running."), 409

    threading.Thread(target=_run_flow, daemon=True).start()
    log.info("Charging flow triggered by %s", request.remote_addr)
    return jsonify(
        status="accepted",
        message="Charging flow started. Result will be emailed to "
                + os.environ["LOGIN_EMAIL"]), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEBHOOK_PORT)
