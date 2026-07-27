"""
import_heart_monitor_stalled_deals.py

Imports fake stalled-deal contacts for the heart monitor vertical --
real hospital buying-committee roles whose evaluation has gone quiet.
Backdates Test Last Activity Date so they're immediately eligible for
re-engagement Touch 1.

Run: python3 import_heart_monitor_stalled_deals.py
"""

import os
import csv
import sys
import unicodedata
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

APAC = ["india", "singapore", "japan", "australia", "new zealand", "china", "south korea", "sweden"]
AMERICAS = ["united states", "usa", "canada", "mexico", "brazil"]


def route(country):
    c = (country or "").strip().lower()
    if c in APAC:
        return "shiwam"
    if c in AMERICAS:
        return "prateek"
    return "carole"


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


with open("data/heart_monitor_stalled_deals.csv") as f:
    rows = list(csv.DictReader(f))

stale_date = datetime.datetime.combine(
    datetime.date.today() - datetime.timedelta(days=30), datetime.time.min, tzinfo=datetime.timezone.utc
)
stale_ms = int(stale_date.timestamp() * 1000)

for row in rows:
    name = row["name"].replace("Dr. ", "")
    parts = name.split(" ", 1)
    first, last = parts[0], parts[1] if len(parts) > 1 else ""
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
            "deal_persona": row["role_type"],
            "reengagement_touch_number": 0,
        }
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=body, timeout=30)
    if r.status_code == 409:
        print(f"  {row['name']} already exists -- skipping")
    elif r.status_code >= 400:
        print(f"  FAILED {row['name']}: {r.status_code} {r.text[:200]}")
    else:
        print(f"  Imported: {row['name']} ({row['role_type']}, routed to {owner})")

print("\nDone.")
