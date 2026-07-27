"""
weekly_digest.py

Posts a weekly aggregate summary to Slack -- counts only, never individual
message content or per-person details (matches the GTM doc's design: the
digest is a leadership visibility tool, not a place to see reps' private
message threads).

Run: python3 weekly_digest.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "lead-pipeline-digest").strip()

if not HUBSPOT_KEY:
    print("No HUBSPOT_SERVICE_KEY in .env")
    sys.exit(1)
if not SLACK_TOKEN:
    print("No SLACK_BOT_TOKEN in .env")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"


def count_by_filter(property_name, operator, value=None):
    filt = {"propertyName": property_name, "operator": operator}
    if value is not None:
        filt["value"] = value
    body = {"filterGroups": [{"filters": [filt]}], "limit": 1}
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("total", 0)


def get_apollo_status():
    try:
        from apollo_credits import get_spent_today, get_spent_this_month, DAILY_LIMIT, MONTHLY_LIMIT
        return get_spent_today(), DAILY_LIMIT, get_spent_this_month(), MONTHLY_LIMIT
    except Exception:
        return None


print("Gathering stats from HubSpot...")

top_count = count_by_filter("outreach_tier", "EQ", "Top")
mid_count = count_by_filter("outreach_tier", "EQ", "Mid")
low_count = count_by_filter("outreach_tier", "EQ", "Low")
replied_count = count_by_filter("outreach_tier", "EQ", "Replied - In Sales Cycle")
apollo_new = count_by_filter("source_event", "EQ", "Apollo Prospecting")
stalled_total = count_by_filter("source_event", "EQ", "Stalled Deal Test")

apollo_status = get_apollo_status()

message_lines = [
    "*:bar_chart: Weekly Lead Activation Digest*",
    "",
    f"*Pipeline breakdown:*",
    f"  • Top tier: {top_count}",
    f"  • Mid tier: {mid_count}",
    f"  • Low tier: {low_count}",
    f"  • Replied / in active sales cycle: {replied_count}",
    "",
    f"*New prospects (Apollo):* {apollo_new} imported to date",
    f"*Stalled deals in re-engagement motion:* {stalled_total}",
]

if apollo_status:
    spent_today, daily_limit, spent_month, monthly_limit = apollo_status
    message_lines += ["", f"*Apollo credits:* {spent_today}/{daily_limit} today · {spent_month}/{monthly_limit} this month"]

message_lines += ["", "_Generated automatically by the lead activation pipeline._"]
message_text = "\n".join(message_lines)

print("\nPosting to Slack...")
resp = requests.post(
    "https://slack.com/api/chat.postMessage",
    headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"},
    json={"channel": SLACK_CHANNEL, "text": message_text},
    timeout=30,
)
result = resp.json()
if result.get("ok"):
    print(f"Posted successfully to #{SLACK_CHANNEL}")
else:
    print(f"Slack error: {result.get('error')}")
    if result.get("error") == "channel_not_found":
        print(f"  Check that #{SLACK_CHANNEL} exists and the bot has been invited to it (/invite @YourBotName).")
    if result.get("error") == "not_in_channel":
        print(f"  The bot needs to be invited to #{SLACK_CHANNEL} first: /invite @YourBotName")
