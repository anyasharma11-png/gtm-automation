"""
cleanup_old_test_data.py

Deletes ALL old pharma/biotech fake test contacts from HubSpot, identified
by their Source Event value. This is a hard delete -- irreversible.

Run: python3 cleanup_old_test_data.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

OLD_SOURCE_EVENTS = [
    "BioPharm Summit 2026",
    "PharmaTech Europe 2026",
    "Apollo Prospecting",
    "Stalled Deal Test",
    "Reply Detection Test",
]

def find_contacts_by_source_event(value):
    body = {
        "filterGroups": [{"filters": [{"propertyName": "source_event", "operator": "EQ", "value": value}]}],
        "properties": ["firstname", "lastname", "email"],
        "limit": 100,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])

all_contacts = []
for value in OLD_SOURCE_EVENTS:
    matches = find_contacts_by_source_event(value)
    print(f"Found {len(matches)} contact(s) with Source Event = '{value}'")
    all_contacts.extend(matches)

if not all_contacts:
    print("\nNothing to delete.")
    sys.exit(0)

print(f"\nTotal: {len(all_contacts)} contact(s) to delete.")
confirm = input("Type DELETE to confirm permanent deletion: ")
if confirm.strip() != "DELETE":
    print("Cancelled -- nothing deleted.")
    sys.exit(0)

deleted, failed = 0, 0
for c in all_contacts:
    p = c["properties"]
    r = requests.delete(f"{BASE}/crm/v3/objects/contacts/{c['id']}", headers=HEADERS, timeout=30)
    if r.status_code < 400:
        print(f"  Deleted: {p.get('firstname','')} {p.get('lastname','')}")
        deleted += 1
    else:
        print(f"  FAILED: {p.get('firstname','')} {p.get('lastname','')} -- {r.status_code}")
        failed += 1

print(f"\nDone. {deleted} deleted, {failed} failed.")
