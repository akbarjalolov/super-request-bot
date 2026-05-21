#!/usr/bin/env python3
"""
Run this ONCE to:
1. Create a Pub/Sub topic and subscription
2. Grant Gmail permission to publish to the topic
3. Set up Gmail watch (push notifications)

Requirements:
  - service_account.json in the same folder
  - All env vars set (see .env.example)
"""

import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import pubsub_v1

SCOPES       = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
PROJECT_ID          = os.environ["GOOGLE_CLOUD_PROJECT"]
TOPIC_ID            = "gmail-push"
GMAIL_USER          = os.environ["GMAIL_USER"]
WEBHOOK_URL         = os.environ["WEBHOOK_URL"]
SERVICE_ACCOUNT_JSON = os.environ["SERVICE_ACCOUNT_JSON"]


def get_gmail_service():
    sa_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    delegated = creds.with_subject(GMAIL_USER)
    return build("gmail", "v1", credentials=delegated)


def setup():
    topic_name = f"projects/{PROJECT_ID}/topics/{TOPIC_ID}"

    # 1. Create Pub/Sub topic
    publisher = pubsub_v1.PublisherClient()
    try:
        publisher.create_topic(request={"name": topic_name})
        print(f"✅ Topic created: {topic_name}")
    except Exception as e:
        if "AlreadyExists" in str(e):
            print(f"ℹ️  Topic already exists: {topic_name}")
        else:
            raise

    # 2. Grant Gmail permission to publish
    policy = publisher.get_iam_policy(request={"resource": topic_name})
    binding = policy.bindings.add()
    binding.role = "roles/pubsub.publisher"
    binding.members.append("serviceAccount:gmail-api-push@system.gserviceaccount.com")
    publisher.set_iam_policy(request={"resource": topic_name, "policy": policy})
    print("✅ IAM policy set — Gmail can publish to topic.")

    # 3. Create push subscription → your webhook
    subscriber = pubsub_v1.SubscriberClient()
    subscription_name = f"projects/{PROJECT_ID}/subscriptions/{TOPIC_ID}-sub"
    try:
        subscriber.create_subscription(
            request={
                "name": subscription_name,
                "topic": topic_name,
                "push_config": {"push_endpoint": WEBHOOK_URL},
                "ack_deadline_seconds": 30,
            }
        )
        print(f"✅ Push subscription created → {WEBHOOK_URL}")
    except Exception as e:
        if "AlreadyExists" in str(e):
            print("ℹ️  Subscription already exists.")
        else:
            raise

    # 4. Tell Gmail to watch and push to Pub/Sub
    service = get_gmail_service()
    resp = service.users().watch(
        userId=GMAIL_USER,
        body={"labelIds": ["INBOX"], "topicName": topic_name}
    ).execute()
    print(f"✅ Gmail watch active. Expiry: {resp.get('expiration')} ms epoch")
    print("\n⚠️  Gmail watch expires every 7 days. Run renew_watch.py weekly.")
    print(f"\n✅ Done. Set this in your env:\n   PUBSUB_TOPIC={topic_name}")


if __name__ == "__main__":
    setup()
