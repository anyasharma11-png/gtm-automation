"""
reengagement_pipeline.py

The Funnel Re-engagement motion, separate from New Prospect Activation.
Per the GTM doc: sales-led, ALWAYS routed to a salesperson (never fully
automated, even for lower scores) since these are active/stalled deals,
not fresh leads.

3-touch cadence:
  Touch 1 (Day 0)  - Personal email from AE, references last interaction
  Touch 2 (Day 5)  - Case study / value share
  Touch 3 (Day 12) - Final ask + "break-up" -- closes the loop either way

Trigger: contacts with last_activity_date > 21 days ago.
Tracks progress via 'Reengagement Touch Number' (0/1/2/3) and
'Last Reengagement Date' properties.

Run: python3 reengagement_pipeline.py
"""

import os
import sys
import time
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
if not HUBSPOT_KEY:
    print("No HUBSPOT_SERVICE_KEY found in .env")
    sys.exit(0)
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

APAC = ["india", "singapore", "japan", "australia", "new zealand", "china", "south korea",
        "malaysia", "thailand", "indonesia", "philippines", "vietnam", "kenya"]
AMERICAS = ["united states", "usa", "canada", "mexico", "brazil", "argentina", "chile", "colombia"]
SALESPEOPLE = {"prateek": "Prateek (US/Americas)", "carole": "Carole (Europe/Intl)", "shiwam": "Shiwam (India/APAC)"}

TOUCH_GAP_DAYS = {1: 0, 2: 5, 3: 7}  # days that must pass since the PREVIOUS touch


def route(country):
    c = (country or "").strip().lower()
    if c in APAC:
        return "shiwam"
    if c in AMERICAS:
        return "prateek"
    return "carole"


def find_property_names():
    r = requests.get(f"{BASE}/crm/v3/properties/contacts", headers=HEADERS, timeout=30)
    r.raise_for_status()
    props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
    wanted = {"source event": None, "deal persona": None, "test last activity date": None,
              "reengagement touch number": None, "last reengagement date": None}
    for name, label in props.items():
        norm = label.strip().lower()
        if norm in wanted:
            wanted[norm] = name
    return wanted


def days_since(value):
    if not value:
        return None
    # HubSpot can return date properties as epoch milliseconds OR as an
    # ISO date string ("2026-06-23") depending on context -- handle both.
    if isinstance(value, str) and not value.isdigit():
        dt = datetime.datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    else:
        dt = datetime.datetime.fromtimestamp(int(value) / 1000, tz=datetime.timezone.utc)
    return (datetime.datetime.now(tz=datetime.timezone.utc) - dt).days


def touch_1_email(p, company):
    first = p.get("firstname") or "there"
    return (f"Hi {first} — it's been a bit since we last connected on our conversation about "
            f"{company}'s needs. Wanted to flag we've had some relevant updates since then — "
            f"worth a quick 20-min catch-up?")


def touch_2_email(p, company):
    first = p.get("firstname") or "there"
    return (f"Hi {first} — sharing a case study that felt relevant to what we discussed regarding "
            f"{company}. No pressure, just thought it was worth a look.")


def touch_3_email(p, company):
    first = p.get("firstname") or "there"
    return (f"{first} — still exploring this? If timing has shifted, totally understand — "
            f"happy to reconnect whenever it makes sense. Otherwise I'll assume this isn't a "
            f"priority right now and won't keep following up.")


TOUCH_SUBJECT = {
    1: "Following up — worth a quick catch-up?",
    2: "Thought this might be useful",
    3: "Checking in one last time",
}
TOUCH_BODY_FN = {1: touch_1_email, 2: touch_2_email, 3: touch_3_email}


def log_note(contact_id, text):
    body = {"properties": {"hs_note_body": text, "hs_timestamp": int(time.time() * 1000)},
            "associations": [{"to": {"id": contact_id},
                               "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]}
    r = requests.post(f"{BASE}/crm/v3/objects/notes", headers=HEADERS, json=body, timeout=30)
    return r.status_code < 400


def write_touch_progress(contact_id, touch_number, props):
    today_ms = int(datetime.datetime.combine(
        datetime.date.today(), datetime.time.min, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    body = {"properties": {props["reengagement touch number"]: touch_number,
                            props["last reengagement date"]: today_ms}}
    r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{contact_id}", headers=HEADERS, json=body, timeout=30)
    return r.status_code < 400


def fetch_stalled_contacts(props):
    properties = ["firstname", "lastname", "email", "jobtitle", "company", "country",
                  props["deal persona"], props["reengagement touch number"], props["last reengagement date"]]
    if props.get("test last activity date"):
        properties.append(props["test last activity date"])
    body = {
        "filterGroups": [{"filters": [{"propertyName": props["source event"], "operator": "EQ",
                                        "value": "Stalled Deal Test"}]}],
        "properties": properties,
        "limit": 100,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def run():
    props = find_property_names()
    # 'test last activity date' is OPTIONAL -- it was archived to free up a property
    # slot for Deal Context. It's only needed to start a fresh Touch 1 for brand-new
    # stalled contacts; contacts already mid-cadence don't need it at all.
    required = ["source event", "deal persona", "reengagement touch number", "last reengagement date"]
    missing = [k for k in required if props.get(k) is None]
    if missing:
        print(f"Missing required properties: {missing}")
        print("Create them in HubSpot first (see script docstring for names/types).")
        sys.exit(1)
    if not props.get("test last activity date"):
        print("NOTE: 'Test Last Activity Date' property not found -- brand-new stalled contacts "
              "(touch 0) can't be started fresh this run, but contacts already mid-cadence will "
              "still be processed normally.\n")

    print("=" * 70)
    print("RE-ENGAGEMENT MOTION -- stalled deal contacts")
    print("=" * 70)

    contacts = fetch_stalled_contacts(props)
    print(f"Found {len(contacts)} stalled-deal contact(s).\n")

    try:
        from gmail_client import get_gmail_service, create_draft
        service = get_gmail_service()
    except Exception as e:
        print(f"Gmail not available: {e}")
        return

    for c in contacts:
        p = c["properties"]
        name = f"{p.get('firstname','')} {p.get('lastname','')}".strip()
        current_touch = int(p.get(props["reengagement touch number"]) or 0)
        last_touch_date = p.get(props["last reengagement date"])
        last_activity = p.get(props["test last activity date"]) if props.get("test last activity date") else None

        if current_touch >= 3:
            print(f"  {name}: already completed all 3 touches -- needs manual decision (dead deal or reactivate)")
            continue

        next_touch = current_touch + 1
        required_gap = TOUCH_GAP_DAYS[next_touch]

        if next_touch == 1:
            if not props.get("test last activity date"):
                print(f"  {name}: staleness property unavailable -- allowing Touch 1 unconditionally "
                      f"(cannot verify 21-day threshold, proceeding anyway for this test contact)")
            else:
                days_stale = days_since(last_activity)
                if days_stale is None or days_stale < 21:
                    print(f"  {name}: not stale enough yet ({days_stale} days) -- skipping")
                    continue
        else:
            days_since_last_touch = days_since(last_touch_date)
            if days_since_last_touch is None or days_since_last_touch < required_gap:
                print(f"  {name}: Touch {next_touch} not due yet "
                      f"({days_since_last_touch} days since last touch, needs {required_gap})")
                continue

        owner = route(p.get("country"))
        to_email = p.get("email")
        if not to_email:
            print(f"  SKIP {name} -- no email on file")
            continue

        import re as _re
        if not _re.match(r"^[\x00-\x7F]+@[\x00-\x7F]+\.[\x00-\x7F]+$", to_email):
            print(f"  SKIP {name} -- malformed/non-ASCII email ({to_email}), needs manual fix in HubSpot")
            log_note(c["id"], f"Re-engagement Touch {next_touch} BLOCKED -- email '{to_email}' is malformed. "
                                f"Fix the email on this contact record, then re-run.")
            continue

        subject = TOUCH_SUBJECT[next_touch]
        body_fn = TOUCH_BODY_FN[next_touch]
        body = body_fn(p, p.get("company", "your team")) + \
            f"\n\n---\nDEMO DRAFT for fake/test contact ({name}). Re-engagement Touch {next_touch}."
        create_draft(service, to_email, subject, body)
        write_touch_progress(c["id"], next_touch, props)

        breakup_note = " (final touch -- close deal as dead or reactivate based on response)" if next_touch == 3 else ""
        log_note(c["id"], f"Re-engagement Touch {next_touch} of 3 sent{breakup_note}. "
                            f"Routed to: {SALESPEOPLE.get(owner)}. Pending human review and send.")
        print(f"  {name}: Touch {next_touch} drafted -> routed to {owner}{breakup_note}")

    print("\nDone.")


if __name__ == "__main__":
    run()
