"""
test_connection.py

Proves your HubSpot Service Key works. Read-only — changes nothing.

Setup:
  1. pip install requests python-dotenv
  2. Copy .env.example to .env and paste your Service Key in it
  3. Run: python test_connection.py
"""

import os
import sys

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("Missing packages. Run:  pip install requests python-dotenv")
    sys.exit(1)

load_dotenv()
KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()

if not KEY or KEY == "paste-your-key-here":
    print("No key found. Copy .env.example to .env and paste your Service Key in it.")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {KEY}"}
BASE = "https://api.hubapi.com"

print("Test 1: pulling 5 contacts...")
r = requests.get(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS,
                 params={"limit": 5, "properties": "firstname,lastname,email,jobtitle,country"}, timeout=30)

if r.status_code == 401:
    print("  FAILED (401): key rejected. Check you copied it fully, no extra spaces.")
    sys.exit(1)
if r.status_code == 403:
    print("  FAILED (403): key valid but missing scope. Add crm.objects.contacts.read to the Service Key.")
    sys.exit(1)
r.raise_for_status()

contacts = r.json().get("results", [])
print(f"  SUCCESS — found {len(contacts)} contact(s):")
for c in contacts:
    p = c.get("properties", {})
    name = f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip() or "(no name)"
    print(f"    - {name} · {p.get('jobtitle') or 'no title'} · {p.get('email') or 'no email'}")

print("\nTest 2: listing your contact lists...")
r = requests.post(f"{BASE}/crm/v3/lists/search", headers={**HEADERS, "Content-Type": "application/json"},
                  json={"query": "", "processingTypes": ["MANUAL", "DYNAMIC"], "count": 20}, timeout=30)

if r.status_code == 403:
    print("  FAILED (403): missing scope. Add crm.lists.read to the Service Key.")
    sys.exit(1)
r.raise_for_status()

lists = r.json().get("lists", [])
if not lists:
    print("  SUCCESS — connection works, but no lists exist yet in this account.")
else:
    print(f"  SUCCESS — found {len(lists)} list(s):")
    for l in lists:
        print(f"    - {l.get('name')} (id {l.get('listId')}, {l.get('additionalProperties', {}).get('hs_list_size', '?')} contacts)")

print("\nConnection verified. Your key works and is read-only safe.")
