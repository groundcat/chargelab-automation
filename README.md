# ChargeID Charging Webhook

A webhook service that starts an EV charging session on a ChargeID (EVStart)
charger using a headless Chrome browser. On a GET request with the correct
secret it:

1. Opens `CHARGEID_BASE_URL/CHARGEID_CHARGER_ID` (e.g. `https://charge.id/<charger-id>`)
2. Logs in with the passwordless email flow — enters the login email, polls
   the inbox over IMAP for the one-time verification code (5–6 digits, parsed
   from the email body regardless of wording), and submits it
3. Clicks **Start session** and waits for the session to begin (the site
   gives ~90 s to plug in; the session URL gains `?sessionId=...` once active).
   The parsed verification email is deleted from the inbox afterwards so
   code emails don't accumulate.
4. Emails the outcome (success, already charging, already complete, or
   failure with page content and traceback) to the user via SMTP

## Login session reuse

The email-code login is only performed when necessary. After each run the
browser's login state (AWS Cognito tokens in cookies/localStorage) is saved
to `BROWSER_STATE_PATH` (default `/data/browser_state.json`, kept on a Docker
volume so it survives container restarts). Every run loads that state first
and checks whether the site still shows a **Log in** button:

- **Still logged in** → the whole email round-trip is skipped and the flow
  takes only a few seconds.
- **Session expired / first run** → the full email-code login runs once, and
  the fresh state is saved for next time.

The state is re-saved after every run — including login-skipped ones — so
silently refreshed Cognito tokens are captured and the saved session stays
valid indefinitely with regular use.

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /webhook?secret=...` (also `GET /?secret=...`) | Trigger the charging flow. Returns `202` immediately; the result arrives by email. `401` on bad secret, `409` if a run is already in progress, `429` if within the configured cooldown window. |
| `GET /health` | Liveness check. |

## Charger states handled

- **Available** → starts a session, waits for it to become active
- **Session already active** (Stop button present) → reports it, starts nothing
- **"Time to move your vehicle"** → charging already completed; reports it,
  starts nothing
- **Vehicle not plugged in** → the plug-in countdown expires and a failure
  email is sent

## Configuration

All settings come from `.env` (see the file in this repo):

- `WEBHOOK_PORT`, `WEBHOOK_SECRET`
- `WEBHOOK_COOLDOWN_MINUTES` (optional, default 0 = off) — repeat requests
  within this many minutes of the last successful trigger are ignored with a
  `429`, protecting against accidental double clicks. A failed run resets the
  cooldown so you can retry immediately.
- `CHARGEID_BASE_URL`, `CHARGEID_CHARGER_ID`, `CHARGEID_LOGIN_EMAIL`
- `LOGIN_EMAIL_HOST`, `LOGIN_EMAIL_USERNAME`, `LOGIN_EMAIL`,
  `LOGIN_EMAIL_PASSWORD`, `LOGIN_EMAIL_SECURE_IMAP_PORT` (993),
  `LOGIN_EMAIL_SECURE_SMTP_PORT` (587)
- `TIMEZONE` (used for timestamps in result emails)
- `BROWSER_STATE_PATH` (optional, default `/data/browser_state.json`) — where
  the reusable login session is stored

## Deploy (VPS)

```bash
docker compose up -d --build
```

or with plain Docker:

```bash
docker build -t chargeid-webhook .
docker run -d --name chargeid-webhook --restart unless-stopped \
  --env-file .env -p 80:80 -v chargeid-data:/data chargeid-webhook
```

Then trigger with:

```bash
curl "http://YOUR_VPS/webhook?secret=YOUR_WEBHOOK_SECRET"
```

## Local development

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium
BROWSER_STATE_PATH=/tmp/chargeid_state.json WEBHOOK_PORT=8080 ./venv/bin/python app.py
```

## Files

- [app.py](app.py) — Flask webhook server (secret check, single-run lock,
  background thread, result email dispatch)
- [charger.py](charger.py) — Playwright automation of the EVStart flow
- [emailer.py](emailer.py) — IMAP code polling (only reads messages that
  arrived after the code was requested) and SMTP result notifications
