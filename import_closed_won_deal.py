"""
import_closed_won_deal.py

Creates ONE fake closed-won deal to test post-sale/customer-success
reporting (template section 2.8), even though the real product is
pre-sale right now. Clearly labeled as simulated test data throughout.

Run: python3 import_closed_won_deal.py
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

COMPANY = "Ridgeway Heart Institute"

# Fake champion contact -- the person who actually "bought"
contact_body = {
    "properties": {
        "firstname": "Patricia",
        "lastname": "Kowalczyk",
        "email": "patricia.kowalczyk.ridgewayheartinstitute@example.com",
        "jobtitle": "Chief of Cardiology",
        "company": COMPANY,
        "country": "United States",
        "source_event": "Heart Monitor Prospecting Test",
        "deal_persona": "executive",
        "lead_score": 100,
        "outreach_tier": "Replied - In Sales Cycle",
        "assigned_rep": "Prateek",
        "deal_context": (
            "Facility: hospital, high fit, large. Stage: Closed Won. Value: $280,000. "
            "Use case: Hospital-wide rollout of AI cardiac monitoring across 3 units. "
            "Bottleneck: None -- deal closed successfully."
        ),
    }
}
r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=contact_body, timeout=30)
if r.status_code == 409:
    print("Contact already exists -- skipping creation")
    contact_id = None
elif r.status_code >= 400:
    print(f"FAILED to create contact: {r.status_code} {r.text[:300]}")
    contact_id = None
else:
    contact_id = r.json()["id"]
    print(f"Created contact: Patricia Kowalczyk (contact {contact_id})")

# Fake closed-won deal
deal_body = {
    "properties": {
        "dealname": f"{COMPANY} - AI Cardiac Monitor",
        "amount": 280000,
        "dealstage": "closedwon",
        "pipeline": "default",
    }
}
r = requests.post(f"{BASE}/crm/v3/objects/deals", headers=HEADERS, json=deal_body, timeout=30)
if r.status_code >= 400:
    print(f"FAILED to create deal: {r.status_code} {r.text[:300]}")
else:
    deal_id = r.json()["id"]
    print(f"Created deal: {COMPANY} (closed won, $280,000) -> deal {deal_id}")

    if contact_id:
        assoc_r = requests.put(
            f"{BASE}/crm/v4/objects/contact/{contact_id}/associations/default/deal/{deal_id}",
            headers=HEADERS, timeout=30
        )
        print(f"Associated Patricia Kowalczyk to deal: {'OK' if assoc_r.status_code < 400 else assoc_r.status_code}")

print("\nDone.")
