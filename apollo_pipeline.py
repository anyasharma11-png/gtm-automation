"""
apollo_pipeline.py

Two modes:
  python3 apollo_pipeline.py --enrich    Find missing emails on existing
                                          HubSpot contacts via Apollo, write
                                          back what's found, log gracefully
                                          what isn't.
  python3 apollo_pipeline.py --prospect  Search Apollo for NEW real people
                                          matching our target profile.
                                          Prints results for review only --
                                          nothing is imported automatically.

Requires HUBSPOT_SERVICE_KEY (with crm.objects.contacts.write) and
APOLLO_API_KEY in .env.
"""

import os
import sys
import argparse

import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
if not HUBSPOT_KEY:
    print("No HUBSPOT_SERVICE_KEY found in .env")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

from apollo_client import enrich_person, search_people, enrich_by_id


def find_property_names():
    r = requests.get(f"{BASE}/crm/v3/properties/contacts", headers=HEADERS, timeout=30)
    r.raise_for_status()
    props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
    source_event = None
    for name, label in props.items():
        if label.strip().lower() == "source event":
            source_event = name
    return source_event


def fetch_contacts_missing_email(source_event_prop):
    """Pulls our test contacts (has a Source Event) that have no email on file."""
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": source_event_prop, "operator": "HAS_PROPERTY"},
            {"propertyName": "email", "operator": "NOT_HAS_PROPERTY"},
        ]}],
        "properties": ["firstname", "lastname", "email", "company"],
        "limit": 100,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def write_email_to_hubspot(contact_id, email):
    body = {"properties": {"email": email}}
    r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{contact_id}", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()


def run_enrich():
    source_event_prop = find_property_names()
    if not source_event_prop:
        print("Couldn't find 'Source Event' property.")
        sys.exit(1)

    print("Finding contacts missing an email...")
    missing = fetch_contacts_missing_email(source_event_prop)
    print(f"Found {len(missing)} contact(s) with no email on file.\n")

    if not missing:
        print("Nothing to enrich. (To test this, blank out a contact's email in HubSpot first.)")
        return

    for c in missing:
        p = c["properties"]
        first, last, company = p.get("firstname", ""), p.get("lastname", ""), p.get("company", "")
        print(f"Looking up {first} {last} at {company} via Apollo...")
        try:
            result = enrich_person(first, last, company)
        except RuntimeError as e:
            print(f"  ERROR: {e}")
            continue

        if result:
            write_email_to_hubspot(c["id"], result["email"])
            print(f"  FOUND: {result['email']} ({result['email_status']}) -- written to HubSpot")
        else:
            print(f"  NOT FOUND -- no Apollo match for this person. Skipping gracefully.")


def run_prospect():
    print("Searching Apollo for new candidate contacts...")
    print("Criteria: senior/mid-level titles in pharma/biotech/research\n")

    titles = ["VP", "Director", "Chief Scientific Officer", "Head of Research", "Manager"]
    industries = ["pharmaceutical", "biotechnology"]

    try:
        results = search_people(titles=titles, industries=industries, max_results=10)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    if not results:
        print("No candidates found matching this criteria.")
        return

    print(f"Found {len(results)} candidate(s). REVIEW ONLY -- nothing has been imported to HubSpot.\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['name']} -- {r['title']} at {r['company']} "
              f"({r['company_size'] or '?'} employees)")
        print(f"   Email: {r['email'] or '[locked -- needs enrichment credit to reveal]'}")
        print(f"   LinkedIn: {r['linkedin_url'] or 'n/a'}")
        print(f"   ID: {r['id']} (use --enrich-id {r['id']} to unlock full details)")

    print("\nTo import any of these into HubSpot, review the list above and tell the pipeline which ones.")


def run_enrich_one(person_id):
    print(f"Enriching person {person_id} via Apollo (uses 1 credit)...\n")
    try:
        result = enrich_by_id(person_id)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    if not result:
        print("No match found for that ID.")
        return

    print(f"Name: {result['name']}")
    print(f"Title: {result['title']}")
    print(f"Company: {result['company']} ({result['company_size'] or '?'} employees)")
    print(f"Email: {result['email']} ({result['email_status']})")
    print(f"LinkedIn: {result['linkedin_url']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enrich", action="store_true")
    parser.add_argument("--prospect", action="store_true")
    parser.add_argument("--enrich-id", type=str, default=None, help="Enrich one specific Apollo person ID from a --prospect result")
    args = parser.parse_args()

    if args.enrich_id:
        run_enrich_one(args.enrich_id)
    elif args.enrich:
        run_enrich()
    elif args.prospect:
        run_prospect()
    else:
        print("Specify --enrich, --prospect, or --enrich-id <id>")
