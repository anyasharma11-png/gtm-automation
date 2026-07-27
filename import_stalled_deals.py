"""
import_stalled_deals.py

Imports fake "stalled deal" contacts into HubSpot for testing the
re-engagement motion. These are tagged with:
  - source_event = "Stalled Deal Test" (distinguishes from event leads)
  - a persona (security / platform) matching the doc's two segments
  - a fake email (.example.com) and a Last Activity date > 21 days ago
    so they're immediately eligible for Touch 1

Run: python3 import_stalled_deals.py
"""

import os
import csv
import sys
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

APAC = ["india", "singapore", "japan", "australia", "new zealand", "china", "south korea",
        "malaysia", "thailand", "indonesia", "philippines", "vietnam", "kenya"]
AMERICAS = ["united states", "usa", "canada", "mexico", "brazil", "argentina", "chile", "colombia"]

def route(country):
    c = (country or "").strip().lower()
    if c in APAC:
        return "shiwam"
    if c in AMERICAS:
        return "prateek"
    return "carole"

with open("data/stalled_deals.csv") as f:
    rows = list(csv.DictReader(f))

# 30 days ago -- comfortably past the 21-day re-engagement trigger threshold
stale_date = datetime.datetime.combine(
    datetime.date.today() - datetime.timedelta(days=30), datetime.time.min, tzinfo=datetime.timezone.utc
)
stale_ms = int(stale_date.timestamp() * 1000)

import unicodedata

def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))

for row in rows:
    first, last = row["name"].split(" ", 1)
    first_ascii, last_ascii = strip_accents(first), strip_accents(last)
    company_slug = "".join(ch for ch in row["company"].lower() if ch.isalnum())
    email = f"{first_ascii.lower()}.{last_ascii.lower().replace(' ', '')}.{company_slug}@example.com"
    owner = route(row["country"])

    body = {
        "properties": {
            "firstname": first,
            "lastname": last,
            "email": email,
            "jobtitle": row["title"],
            "company": row["company"],
            "country": row["country"],
            "source_event": "Stalled Deal Test",
            "deal_persona": row["persona"],
            "test_last_activity_date": stale_ms,
            "reengagement_touch_number": 0,
        }
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=body, timeout=30)
    if r.status_code == 409:
        print(f"  {row['name']} already exists -- skipping")
    elif r.status_code >= 400:
        print(f"  FAILED {row['name']}: {r.status_code} {r.text[:200]}")
    else:
        print(f"  Imported: {row['name']} ({row['persona']}, routed to {owner})")
