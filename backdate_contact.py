"""
backdate_contact.py

Quick utility: finds a contact by name and sets their Last Processed Date
to yesterday, so the next funnel.py run picks them up fresh for testing.

Usage: python3 backdate_contact.py "Carlos Hassan"
"""

import os, sys, datetime
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

if len(sys.argv) < 2:
    print('Usage: python3 backdate_contact.py "First Last"')
    sys.exit(1)

name = sys.argv[1]
first, *rest = name.split(" ")
last = " ".join(rest)

r = requests.get(f"{BASE}/crm/v3/properties/contacts", headers=HEADERS, timeout=30)
props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
last_processed_prop = next((n for n, l in props.items() if l.strip().lower() == "last processed date"), None)
if not last_processed_prop:
    print("Couldn't find 'Last Processed Date' property.")
    sys.exit(1)

body = {
    "filterGroups": [{"filters": [
        {"propertyName": "firstname", "operator": "EQ", "value": first},
        {"propertyName": "lastname", "operator": "EQ", "value": last},
    ]}],
    "properties": ["firstname", "lastname"],
    "limit": 1,
}
r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
results = r.json().get("results", [])
if not results:
    print(f"No contact found named '{name}'")
    sys.exit(1)

contact_id = results[0]["id"]
yesterday = datetime.datetime.combine(
    datetime.date.today() - datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.timezone.utc
)
yesterday_ms = int(yesterday.timestamp() * 1000)

r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{contact_id}", headers=HEADERS,
                    json={"properties": {last_processed_prop: yesterday_ms}}, timeout=30)
if r.status_code < 400:
    print(f"Backdated {name} (contact {contact_id}) to yesterday. Run funnel.py to pick them up fresh.")
else:
    print(f"FAILED: {r.status_code} {r.text[:300]}")
