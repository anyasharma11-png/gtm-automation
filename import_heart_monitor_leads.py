"""
import_heart_monitor_leads.py

Imports the 30 fake heart-monitor ICP leads into HubSpot, consolidating
fields to fit within the 10-custom-property limit:
  - deal_persona (reused)  -> role type (executive/technical/procurement/enduser)
  - deal_context (new)     -> facility type/fit/size + deal stage/value +
                               use case + bottleneck, as one readable block

Run: python3 import_heart_monitor_leads.py
"""

import os
import csv
import sys
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
    if c in APAC: return "shiwam"
    if c in AMERICAS: return "prateek"
    return "carole"

with open("data/heart_monitor_leads.csv") as f:
    rows = list(csv.DictReader(f))

imported, skipped, failed = 0, 0, 0
for row in rows:
    owner = route(row["country"])
    rep_label = {"prateek": "Prateek", "carole": "Carole", "shiwam": "Shiwam"}[owner] \
        if row["tier"] == "Top" else "Automated Nurture"

    deal_context = (
        f"Facility: {row['facility_type']}, {row['facility_fit']} fit"
        f"{', large' if row['large_facility'] == 'True' else ''}. "
        f"Stage: {row['deal_stage']}. Value: ${int(row['deal_value']):,}. "
        f"Use case: {row['use_case']}. Bottleneck: {row['bottleneck']}."
    )

    body = {
        "properties": {
            "firstname": row["first_name"],
            "lastname": row["last_name"],
            "email": row["email"],
            "jobtitle": row["title"],
            "company": row["facility"],
            "country": row["country"],
            "source_event": "Heart Monitor Prospecting Test",
            "lead_score": row["score"],
            "outreach_tier": row["tier"],
            "assigned_rep": rep_label,
            "deal_persona": row["role_type"],
            "deal_context": deal_context,
        }
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=body, timeout=30)
    if r.status_code == 409:
        print(f"  {row['first_name']} {row['last_name']} already exists -- skipping")
        skipped += 1
    elif r.status_code >= 400:
        print(f"  FAILED {row['first_name']} {row['last_name']}: {r.status_code} {r.text[:200]}")
        failed += 1
    else:
        print(f"  Imported: {row['first_name']} {row['last_name']} ({row['tier']}, {row['score']}) -> {row['facility']}")
        imported += 1

print(f"\nDone. {imported} imported, {skipped} skipped (already exist), {failed} failed.")
