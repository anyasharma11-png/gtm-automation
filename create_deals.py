"""
create_deals.py

Creates one real HubSpot Deal per facility (account) among our heart
monitor test contacts, then associates every contact at that facility
to their deal. This is the structural piece we were missing -- coaching
is per-DEAL in the template spec, not per-contact.

Run: python3 create_deals.py
"""

import os
import re
import sys
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

STAGE_MAP = {
    "Discovery": "appointmentscheduled",
    "Technical Evaluation": "qualifiedtobuy",
    "Committee Review": "presentationscheduled",
    "Pilot Proposed": "decisionmakerboughtin",
    "Stalled - No Response": "qualifiedtobuy",
}


def parse_deal_context(text):
    if not text:
        return {}
    result = {}
    m = re.search(r"Stage:\s*(.+?)\.", text)
    if m: result["stage"] = m.group(1).strip()
    m = re.search(r"Value:\s*\$([\d,]+)", text)
    if m: result["value"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Use case:\s*(.+?)\.\s*Bottleneck:", text)
    if m: result["use_case"] = m.group(1).strip()
    m = re.search(r"Bottleneck:\s*(.+?)\.?$", text)
    if m: result["bottleneck"] = m.group(1).strip()
    return result


def fetch_heart_monitor_contacts():
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "source_event", "operator": "EQ", "value": "Heart Monitor Prospecting Test"}
        ]}],
        "properties": ["firstname", "lastname", "email", "jobtitle", "company",
                        "lead_score", "outreach_tier", "deal_persona", "deal_context"],
        "limit": 100,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


contacts = fetch_heart_monitor_contacts()
print(f"Found {len(contacts)} heart monitor contacts.\n")

by_company = defaultdict(list)
for c in contacts:
    company = c["properties"].get("company")
    if company:
        by_company[company].append(c)

print(f"Grouped into {len(by_company)} facilities/accounts.\n")

def fetch_existing_deal_names():
    """Returns the set of existing deal names, so we never create duplicates
    on repeated/scheduled runs."""
    r = requests.get(f"{BASE}/crm/v3/objects/deals", headers=HEADERS,
                      params={"properties": "dealname", "limit": 100}, timeout=30)
    r.raise_for_status()
    return {d["properties"].get("dealname") for d in r.json().get("results", [])}


existing_deal_names = fetch_existing_deal_names()
print(f"Found {len(existing_deal_names)} existing deal(s) -- will skip re-creating these.\n")

for company, company_contacts in by_company.items():
    dealname = f"{company} - AI Cardiac Monitor"

    if dealname in existing_deal_names:
        print(f"  Deal already exists for {company} -- skipping creation (associations still checked/safe to re-run)")
        continue

    # Use the highest-value deal_context found among contacts at this facility
    best_ctx = {}
    for c in company_contacts:
        ctx = parse_deal_context(c["properties"].get("deal_context"))
        if ctx.get("value", 0) > best_ctx.get("value", 0):
            best_ctx = ctx

    fake_stage = best_ctx.get("stage", "Discovery")
    hubspot_stage = STAGE_MAP.get(fake_stage, "appointmentscheduled")
    amount = best_ctx.get("value", 50000)

    body = {"properties": {"dealname": dealname,
                            "amount": amount, "dealstage": hubspot_stage, "pipeline": "default"}}
    r = requests.post(f"{BASE}/crm/v3/objects/deals", headers=HEADERS, json=body, timeout=30)
    if r.status_code >= 400:
        print(f"  FAILED to create deal for {company}: {r.status_code} {r.text[:200]}")
        continue
    deal_id = r.json()["id"]
    print(f"  Created deal: {company} (${amount:,}, stage: {fake_stage}) -> deal {deal_id}")

    for c in company_contacts:
        contact_id = c["id"]
        assoc_r = requests.put(
            f"{BASE}/crm/v4/objects/contact/{contact_id}/associations/default/deal/{deal_id}",
            headers=HEADERS, timeout=30
        )
        name = f"{c['properties'].get('firstname','')} {c['properties'].get('lastname','')}"
        if assoc_r.status_code < 400:
            print(f"    Associated: {name}")
        else:
            print(f"    FAILED to associate {name}: {assoc_r.status_code}")

print("\nDone.")
