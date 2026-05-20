import os
import base64
import json
import logging
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import telegram
import asyncio

# ── CONFIG ────────────────────────────────────────────────────────────────────
ALLOWED_SENDER     = "broker.updates@superdispatch.com"
SUBJECT_KEYWORD    = "New request from"
TELEGRAM_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]     # e.g. "-1001234567890"
PUBSUB_TOPIC       = os.environ["PUBSUB_TOPIC"]          # e.g. "projects/YOUR_PROJECT/topics/gmail-push"
GMAIL_USER         = os.environ["GMAIL_USER"]            # info@xpresstransportation.com
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

BODY_PREVIEW_CHARS = 300
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
bot = telegram.Bot(token=TELEGRAM_TOKEN)


# ── GMAIL AUTH (Service Account) ──────────────────────────────────────────────
def get_gmail_service():
    """
    Authenticates using a service account with domain-wide delegation.
    Impersonates GMAIL_USER so we can read their inbox.
    """
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
    )
    # Impersonate the target mailbox
    delegated_creds = creds.with_subject(GMAIL_USER)
    return build("gmail", "v1", credentials=delegated_creds)


# ── EMAIL PARSING ─────────────────────────────────────────────────────────────
def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def extract_body(payload):
    """Recursively extract plain text body from Gmail payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = extract_body(part)
        if result:
            return result
    return ""


def clean_body(text):
    """Strip excessive whitespace and blank lines."""
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    return "\n".join(lines)


def matches_filters(sender, subject):
    sender_ok  = ALLOWED_SENDER.lower() in sender.lower()
    subject_ok = SUBJECT_KEYWORD.lower() in subject.lower()
    return sender_ok and subject_ok


# ── TELEGRAM SENDER ───────────────────────────────────────────────────────────
async def send_to_telegram(subject, body_preview):
    message = (
        f"📧 *{telegram.helpers.escape_markdown(subject, version=2)}*\n\n"
        f"{telegram.helpers.escape_markdown(body_preview, version=2)}"
    )
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
    )
    log.info("Message sent to Telegram group.")


# ── EMAIL PROCESSOR ───────────────────────────────────────────────────────────
def process_new_emails():
    service = get_gmail_service()

    query = f'is:unread from:{ALLOWED_SENDER} subject:"{SUBJECT_KEYWORD}"'
    result = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
    messages = result.get("messages", [])

    if not messages:
        log.info("No matching unread emails found.")
        return

    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = msg["payload"].get("headers", [])
        sender  = get_header(headers, "From")
        subject = get_header(headers, "Subject")

        if not matches_filters(sender, subject):
            log.info(f"Skipping — mismatch: {sender} | {subject}")
            continue

        raw_body = extract_body(msg["payload"])
        clean    = clean_body(raw_body)
        preview  = clean[:BODY_PREVIEW_CHARS] + ("…" if len(clean) > BODY_PREVIEW_CHARS else "")

        log.info(f"Forwarding: {subject}")
        asyncio.run(send_to_telegram(subject, preview))

        # Mark as read so we don't re-process it
        service.users().messages().modify(
            userId="me",
            id=msg_ref["id"],
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()


# ── WEBHOOK (Pub/Sub push endpoint) ──────────────────────────────────────────
@app.route("/pubsub/push", methods=["POST"])
def pubsub_push():
    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        log.warning("Invalid Pub/Sub message received.")
        return jsonify({"error": "bad request"}), 400

    pubsub_message = envelope["message"]
    data = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    log.info(f"Pub/Sub notification: {data}")

    try:
        process_new_emails()
    except Exception as e:
        log.error(f"Error processing emails: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running"}), 200


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log.info(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
