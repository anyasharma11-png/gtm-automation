"""
apollo_client.py

Two Apollo.io capabilities:
  1. enrich_person()  - given a name + company, find their email (fills gaps
                         on contacts that already exist in HubSpot)
  2. search_people()   - given criteria (title, industry, company size),
                         find NEW real people not yet in HubSpot

Both are read-only against Apollo (lookups/searches). Nothing gets written
to HubSpot from this file directly -- the calling code in run_pipeline.py
decides what to do with the results.
"""

import os
import requests

APOLLO_KEY = os.getenv("APOLLO_API_KEY", "").strip()
BASE = "https://api.apollo.io/api/v1"


def enrich_person(first_name: str, last_name: str, company: str):
    """
    Looks up a specific person by name + company. Returns a dict with
    email/title/linkedin_url if found, or None if no match.
    Consumes an Apollo credit per call that finds a match.
    """
    if not APOLLO_KEY:
        raise RuntimeError("APOLLO_API_KEY not set in .env")

    from apollo_credits import check_budget, record_spend
    check_budget(credits_needed=1)

    headers = {"X-Api-Key": APOLLO_KEY, "Content-Type": "application/json"}
    body = {"first_name": first_name, "last_name": last_name, "organization_name": company}
    r = requests.post(f"{BASE}/people/match", headers=headers, json=body, timeout=30)

    if r.status_code == 403:
        raise RuntimeError("Apollo rejected the request (403) -- check your API key/plan permissions.")
    r.raise_for_status()

    person = r.json().get("person")
    if not person or not person.get("email"):
        return None

    record_spend(1)

    return {
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "title": person.get("title"),
        "linkedin_url": person.get("linkedin_url"),
    }


def search_people(titles: list, industries: list = None, min_company_size: int = None, max_results: int = 10):
    """
    Searches Apollo's database for NEW people matching criteria -- these are
    real people not yet in your HubSpot. Returns a list of candidate dicts.
    Does NOT write anything anywhere -- purely a search, for human review.
    """
    if not APOLLO_KEY:
        raise RuntimeError("APOLLO_API_KEY not set in .env")

    headers = {"X-Api-Key": APOLLO_KEY, "Content-Type": "application/json"}
    body = {
        "person_titles": titles,
        "page": 1,
        "per_page": max_results,
    }
    if industries:
        body["q_organization_keyword_tags"] = industries

    # Use api_search, not search -- 'search' 403s on Basic-tier Apollo plans
    r = requests.post(f"{BASE}/mixed_people/api_search", headers=headers, json=body, timeout=30)

    if r.status_code == 403:
        raise RuntimeError("Apollo rejected the search (403) -- this endpoint may need a higher plan tier.")
    r.raise_for_status()

    people = r.json().get("people", [])

    if os.getenv("DEBUG_APOLLO", "").lower() == "true" and people:
        import json
        print("\n--- DEBUG: raw first result from Apollo ---")
        print(json.dumps(people[0], indent=2)[:2000])
        print("--- end debug ---\n")

    results = []
    for p in people:
        org = p.get("organization") or {}
        name = p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "(name not returned)"
        linkedin = p.get("linkedin_url") or p.get("person_linkedin_url") or org.get("linkedin_url")
        company_size = org.get("estimated_num_employees") or org.get("num_employees") or org.get("employee_count")
        results.append({
            "id": p.get("id"),  # needed to enrich this specific person later
            "name": name,
            "title": p.get("title"),
            "company": org.get("name"),
            "company_size": company_size,
            "email": p.get("email"),  # often locked/hidden until enriched
            "linkedin_url": linkedin,
        })
    return results


def enrich_by_id(person_id: str):
    """
    Enriches a specific person found via search, using their Apollo ID
    (returned by search_people). This unlocks full name, email, LinkedIn,
    etc. that search results keep locked. Consumes an Apollo credit.
    """
    if not APOLLO_KEY:
        raise RuntimeError("APOLLO_API_KEY not set in .env")

    from apollo_credits import check_budget, record_spend
    check_budget(credits_needed=1)  # raises RuntimeError if this would exceed daily/monthly limit

    headers = {"X-Api-Key": APOLLO_KEY, "Content-Type": "application/json"}
    body = {"id": person_id}
    r = requests.post(f"{BASE}/people/match", headers=headers, json=body, timeout=30)

    if r.status_code == 403:
        raise RuntimeError("Apollo rejected the request (403) -- check your API key/plan permissions.")
    r.raise_for_status()

    person = r.json().get("person")
    if not person:
        return None

    record_spend(1)  # only record spend once we know the call succeeded

    org = person.get("organization") or {}
    return {
        "name": person.get("name"),
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "title": person.get("title"),
        "linkedin_url": person.get("linkedin_url"),
        "company": org.get("name"),
        "company_size": org.get("estimated_num_employees"),
    }
