#!/usr/bin/env python3
"""
Gmail watch expires every 7 days. Run weekly via cron or scheduler.

Cron example (every Sunday 08:00):
  0 8 * * 0 cd /path/to/bot && python renew_watch.py >> renew.log 2>&1
"""

import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES       = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
GMAIL_USER   = os.environ["GMAIL_USER"]
PUBSUB_TOPIC = os.environ["PUBSUB_TOPIC"]
SA_FILE      = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")


def renew():
    creds     = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    delegated = creds.with_subject(GMAIL_USER)
    service   = build("gmail", "v1", credentials=delegated)

    resp = service.users().watch(
        userId=GMAIL_USER,
        body={"labelIds": ["INBOX"], "topicName": PUBSUB_TOPIC}
    ).execute()
    print(f"✅ Gmail watch renewed. New expiry: {resp.get('expiration')} ms epoch")


if __name__ == "__main__":
    renew()
