"""
funnel.py

The full lead activation funnel, end to end:

  1. APOLLO SEARCH    -> find new real candidates matching target criteria
  2. APOLLO ENRICH     -> unlock full contact info for a capped number of them
                          (costs Apollo credits -- kept small on purpose)
  3. IMPORT TO HUBSPOT -> enriched candidates become new HubSpot contacts
  4. SCORE EVERYONE    -> pulls ALL contacts with a Source Event (existing +
                          newly imported) and scores them, same point table
                          used throughout this project
  5. ROUTE BY TIER:
       - Low/Mid tier  -> tagged for automated nurture in HubSpot (no Gmail
                          draft -- this is where a marketing sequence tool
                          would take over in the real system)
       - Top tier      -> personalized Gmail draft auto-created, routed to
                          the correct salesperson by region, logged back to
                          HubSpot. Human still reviews and sends -- nothing
                          auto-sends.

Run: python3 funnel.py
"""

import os
import re
import sys
import time
import json

import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
if not HUBSPOT_KEY:
    print("No HUBSPOT_SERVICE_KEY found in .env")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

from apollo_client import search_people, enrich_by_id

# ---------- Config ----------
MAX_NEW_PROSPECTS = 3       # how many Apollo candidates to enrich+import per run (credit-conscious)
MAX_DRAFTS_PER_RUN = 10     # safety cap so a run never floods your Gmail Drafts folder
PROSPECT_TITLES = ["Chief of Cardiology", "Director of Cardiac Services", "Chief Nursing Officer",
                   "Chief Medical Information Officer", "Director of Biomedical Engineering", "CFO"]
PROSPECT_INDUSTRIES = ["hospital & health care", "medical devices"]

BUYER_KEYWORDS = ["cso", "chief", "vp", "v.p.", "vice president", "director", "cto", "cio", "head of"]

APAC = ["india", "singapore", "japan", "australia", "new zealand", "china", "south korea",
        "malaysia", "thailand", "indonesia", "philippines", "vietnam"]
AMERICAS = ["united states", "usa", "canada", "mexico", "brazil", "argentina", "chile", "colombia"]
SALESPEOPLE = {"prateek": "Prateek (US/Americas)", "carole": "Carole (Europe/Intl)", "shiwam": "Shiwam (India/APAC)"}


# ==================== STAGE 1+2: APOLLO SEARCH + ENRICH ====================
SEEN_IDS_FILE = "apollo_seen_ids.json"


def _load_seen_ids():
    if not os.path.exists(SEEN_IDS_FILE):
        return set()
    with open(SEEN_IDS_FILE, "r") as f:
        try:
            return set(json.load(f))
        except Exception:
            return set()


def _save_seen_ids(ids):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


def already_in_hubspot(email):
    """Checks if a contact with this email already exists in HubSpot."""
    body = {"filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
            "properties": ["email"], "limit": 1}
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return len(r.json().get("results", [])) > 0


def stage_prospect_and_enrich():
    from apollo_credits import print_status
    print_status()

    print("=" * 70)
    print("STAGE 1: Apollo Search -- finding new candidates")
    print("=" * 70)
    try:
        candidates = search_people(titles=PROSPECT_TITLES, industries=PROSPECT_INDUSTRIES, max_results=10)
    except RuntimeError as e:
        print(f"Apollo search failed: {e}")
        return []
    print(f"Found {len(candidates)} candidates.\n")

    seen_ids = _load_seen_ids()
    unseen_candidates = [c for c in candidates if c.get("id") and c["id"] not in seen_ids]
    already_seen_count = len(candidates) - len(unseen_candidates)
    if already_seen_count:
        print(f"({already_seen_count} candidate(s) already enriched in a previous run -- skipping, no credits spent)\n")

    print("=" * 70)
    print("STAGE 2: Apollo Enrich -- unlocking full contact info")
    print("=" * 70)
    enriched = []
    checked = 0
    for c in unseen_candidates:
        if len(enriched) >= MAX_NEW_PROSPECTS:
            break
        checked += 1
        try:
            full = enrich_by_id(c["id"])
        except RuntimeError as e:
            print(f"  STOPPING: {e}")
            break  # budget exceeded or other hard error
        seen_ids.add(c["id"])  # never spend a credit on this person again, regardless of outcome
        if not full or not full.get("email"):
            print(f"  No email found for {c['name']}, skipping")
            continue
        if already_in_hubspot(full["email"]):
            print(f"  {full['name']} ({full['email']}) already in HubSpot -- skipping")
            continue
        print(f"  Enriched (NEW): {full['name']} -- {full['email']}")
        enriched.append(full)

    _save_seen_ids(seen_ids)
    print(f"\nChecked {checked} new candidate(s), found {len(enriched)} genuinely new.")
    return enriched


# ==================== STAGE 3: IMPORT TO HUBSPOT ====================
def stage_import_to_hubspot(enriched_people):
    print("\n" + "=" * 70)
    print("STAGE 3: Importing new contacts into HubSpot")
    print("=" * 70)
    for person in enriched_people:
        name_parts = (person.get("name") or "").split(" ", 1)
        first = name_parts[0] if name_parts else ""
        last = name_parts[1] if len(name_parts) > 1 else ""
        body = {
            "properties": {
                "firstname": first,
                "lastname": last,
                "email": person["email"],
                "jobtitle": person.get("title", ""),
                "company": person.get("company", ""),
                "source_event": "Apollo Prospecting",
            }
        }
        r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=body, timeout=30)
        if r.status_code == 409:
            print(f"  {person['name']} already exists in HubSpot -- skipping import")
        elif r.status_code >= 400:
            print(f"  FAILED to import {person['name']}: {r.status_code} {r.text[:150]}")
        else:
            print(f"  Imported: {person['name']} ({person['email']})")
    print()


# ==================== STAGE 4: SCORE EVERYONE ====================
def find_property_names():
    r = requests.get(f"{BASE}/crm/v3/properties/contacts", headers=HEADERS, timeout=30)
    r.raise_for_status()
    props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
    result = {"source_event": None, "employee_count": None, "last_processed": None,
              "touch_number": None, "last_touch_date": None, "deal_context": None}
    for name, label in props.items():
        norm = label.strip().lower()
        if norm == "source event":
            result["source_event"] = name
        if norm == "employee count":
            result["employee_count"] = name
        if norm == "last processed date":
            result["last_processed"] = name
        if norm == "reengagement touch number":
            result["touch_number"] = name
        if norm == "last reengagement date":
            result["last_touch_date"] = name
        if norm == "deal context":
            result["deal_context"] = name
    return result


EXECUTIVE_TITLES = ["chief of cardiology", "director of cardiac services", "chief nursing officer",
                    "cfo", "vp clinical operations", "chief medical officer"]
TECHNICAL_TITLES = ["chief medical information officer", "cmio", "vp health it",
                    "director of biomedical engineering", "director of clinical engineering"]
PROCUREMENT_TITLES = ["director of supply chain", "vp procurement", "procurement"]
ENDUSER_TITLES = ["icu medical director", "nurse manager", "cardiac unit nurse manager"]
ROLE_BASE = {"executive": 40, "technical": 32, "procurement": 24, "enduser": 18}

HIGH_FIT_KEYWORDS = ["hospital", "medical center", "academic medical", "university hospital", "remote monitoring"]
MID_FIT_KEYWORDS = ["clinic", "cardiology associates", "ambulatory surgical", "diagnostic"]
LOW_FIT_KEYWORDS = ["rehab", "nursing", "skilled nursing"]
FIT_MULTIPLIER = {"high": 1.0, "mid": 0.65, "low": 0.35}


def classify_role(title):
    """Returns (role_type, base_points) inferred from job title."""
    t = (title or "").lower()
    for k in EXECUTIVE_TITLES:
        if k in t:
            return "executive", ROLE_BASE["executive"]
    for k in TECHNICAL_TITLES:
        if k in t:
            return "technical", ROLE_BASE["technical"]
    for k in PROCUREMENT_TITLES:
        if k in t:
            return "procurement", ROLE_BASE["procurement"]
    for k in ENDUSER_TITLES:
        if k in t:
            return "enduser", ROLE_BASE["enduser"]
    return "unknown", 0


def classify_facility_fit(company):
    """Returns 'high'/'mid'/'low' inferred from facility name -- a rough
    heuristic since Apollo/HubSpot don't have a structured 'facility type'
    field. Defaults to 'mid' if nothing matches, rather than assuming worst."""
    c = (company or "").lower()
    for k in HIGH_FIT_KEYWORDS:
        if k in c:
            return "high"
    for k in LOW_FIT_KEYWORDS:
        if k in c:
            return "low"
    for k in MID_FIT_KEYWORDS:
        if k in c:
            return "mid"
    return "mid"


def is_large_facility(company, employee_count_prop, p):
    """Large facility signal -- from Employee Count if available, otherwise
    inferred from facility name (hospitals/medical centers default large)."""
    if employee_count_prop and safe_int(p.get(employee_count_prop)) >= 300:
        return True
    c = (company or "").lower()
    return any(k in c for k in ["hospital", "medical center", "academic medical", "university hospital"])


def safe_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def score_contact(p, employee_count_prop):
    breakdown = []
    role_type, base = classify_role(p.get("jobtitle"))
    if base:
        breakdown.append((f"Role: {role_type}", base))

    if is_large_facility(p.get("company"), employee_count_prop, p):
        breakdown.append(("Large facility", 15))
    if p.get("email_engaged") == "true" or p.get("hs_email_open") == "true":
        breakdown.append(("Email engaged", 10))
    if p.get("visited_website") == "true":
        breakdown.append(("Visited website", 10))

    raw_total = sum(x for _, x in breakdown)
    fit = classify_facility_fit(p.get("company"))
    score = round(raw_total * FIT_MULTIPLIER[fit])
    breakdown.append((f"Facility fit: {fit} (x{FIT_MULTIPLIER[fit]})", score - raw_total))
    return score, breakdown


def get_tier(score):
    return "top" if score >= 55 else "mid" if score >= 25 else "low"


def route(country):
    c = (country or "").strip().lower()
    if not c:
        return None
    if c in APAC:
        return "shiwam"
    if c in AMERICAS:
        return "prateek"
    return "carole"


def is_valid_email(email):
    """Basic ASCII-only email format check -- catches malformed addresses
    (e.g. accented characters that slipped through name-based generation)
    before we try to create a Gmail draft with them."""
    return bool(email) and bool(re.match(r"^[\x00-\x7F]+@[\x00-\x7F]+\.[\x00-\x7F]+$", email))


def stage_score_everyone(source_event_prop, employee_count_prop, last_processed_prop,
                          touch_number_prop=None, last_touch_date_prop=None, deal_context_prop=None):
    print("=" * 70)
    print("STAGE 4: Scoring every contact (existing + newly imported)")
    print("=" * 70)
    contacts, after = [], None
    props = ["firstname", "lastname", "email", "jobtitle", "company", "industry", "country",
              "outreach_tier", "lead_score", source_event_prop]
    if employee_count_prop:
        props.append(employee_count_prop)
    if last_processed_prop:
        props.append(last_processed_prop)
    if touch_number_prop:
        props.append(touch_number_prop)
    if last_touch_date_prop:
        props.append(last_touch_date_prop)
    if deal_context_prop:
        props.append(deal_context_prop)
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": source_event_prop, "operator": "HAS_PROPERTY"}]}],
                "properties": props, "limit": 100}
        if after:
            body["after"] = after
        r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        contacts.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    import datetime
    today_str = datetime.date.today().isoformat()

    scored = []
    already_done = 0
    already_replied = 0
    for c in contacts:
        p = c["properties"]

        # A contact who has already replied is handled by check_replies.py,
        # not here -- preserve their boosted score/tier instead of silently
        # overwriting it back to a plain Top/Mid/Low on the next run.
        if p.get("outreach_tier") == "Replied - In Sales Cycle":
            already_replied += 1
            scored.append({"contact_id": c["id"], "props": p, "score": int(p.get("lead_score") or 0),
                            "tier": "replied", "breakdown": [], "owner": None, "processed_today": False})
            continue

        score, breakdown = score_contact(p, employee_count_prop)
        tier = get_tier(score)
        owner = route(p.get("country"))

        # Check if already processed today -- HubSpot date properties return
        # an ISO timestamp like "2026-07-23T00:00:00.000Z"
        last_processed_raw = p.get(last_processed_prop) if last_processed_prop else None
        processed_today = bool(last_processed_raw and last_processed_raw[:10] == today_str)
        if processed_today:
            already_done += 1

        scored.append({"contact_id": c["id"], "props": p, "score": score, "tier": tier,
                        "breakdown": breakdown, "owner": owner, "processed_today": processed_today})

    print(f"Scored {len(scored)} total contacts. ({already_done} already processed today -- will skip, "
          f"{already_replied} already replied -- preserved as-is)\n")
    return scored


# ==================== STAGE 5: ROUTE BY TIER ====================
def is_buyer(title):
    t = (title or "").lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", t) for k in BUYER_KEYWORDS)


def draft(p, source_event_prop):
    first = p.get("firstname") or "there"
    company = p.get("company") or "your team"
    event = p.get(source_event_prop) or "the event"
    if is_buyer(p.get("jobtitle")):
        return (f"Hi {first}, great connecting around {event}. At [YourCompany] we help teams like "
                f"{company}'s streamline [core value prop] without adding operational overhead. "
                f"No ask here — just glad to be connected.")
    return (f"Hi {first}, enjoyed seeing {company}'s work come up around {event}. We work on "
            f"[technical value prop] and thought it'd be good to connect with people hands-on in this space.")


def draft_mid_tier(p, source_event_prop):
    """
    Simpler, more generic template for Mid-tier contacts -- automated
    sequence touch 1, not worth an AE's personalized attention but still
    a real, reviewable draft (never auto-sent).
    """
    first = p.get("firstname") or "there"
    event = p.get(source_event_prop) or "the event"
    return (f"Hi {first}, thanks for connecting at {event}. We help teams in your space work more "
            f"efficiently — happy to share more if useful. No pressure either way.")


def parse_deal_context(deal_context_text):
    """
    Extracts use_case and bottleneck from the consolidated Deal Context
    text field written at import time. Returns generic fallbacks if the
    contact has no Deal Context (e.g. a new Apollo prospect, not one of
    our pre-loaded test leads).
    """
    if not deal_context_text:
        return None, None
    use_case = None
    bottleneck = None
    m = re.search(r"Use case:\s*(.+?)\.\s*Bottleneck:", deal_context_text)
    if m:
        use_case = m.group(1).strip()
    m = re.search(r"Bottleneck:\s*(.+?)\.?$", deal_context_text)
    if m:
        bottleneck = m.group(1).strip()
    return use_case, bottleneck


NURTURE_TOUCH_GAP_DAYS = {1: 0, 2: 3, 3: 7}  # faster cadence than re-engagement -- these are warm, not stalled


def draft_nurture_touch(p, touch_number, use_case, bottleneck):
    """
    Generates one touch of a 3-touch nurture campaign for a Top-tier
    contact, each referencing their specific bottleneck (pulled from Deal
    Context) and connecting it to the product -- not a generic template.
    Falls back to the standard persona-based draft if no bottleneck data
    exists for this contact (e.g. a fresh Apollo prospect).
    """
    first = p.get("firstname") or "there"
    company = p.get("company") or "your team"

    if not bottleneck or not use_case:
        return f"Great connecting at {company}. Would love to learn more about what you're working on."

    if touch_number == 1:
        return (f"Hi {first}, in looking into {company}'s work on {use_case.lower()}, "
                f"one thing that stood out was: {bottleneck.lower()}. That's exactly the kind of "
                f"gap our AI-powered cardiac monitoring platform is built to close — happy to share "
                f"how other teams in a similar spot have approached it, no pressure either way.")
    if touch_number == 2:
        return (f"Hi {first}, following up on {use_case.lower()} — specifically, teams facing "
                f"'{bottleneck.lower()}' have typically found our platform helps by giving clinical "
                f"and technical stakeholders the same real-time data view, which shortens the "
                f"evaluation cycle instead of adding to it. Happy to walk through a short case study "
                f"if useful.")
    return (f"{first} — last note on this. If '{bottleneck.lower()}' is still the blocker on "
            f"{use_case.lower()}, a 20-minute conversation might be worth it to see whether our "
            f"platform actually removes that specific obstacle for {company}. If timing's off, "
            f"totally understand — happy to reconnect whenever it's right.")


def log_note(contact_id, text):
    body = {
        "properties": {"hs_note_body": text, "hs_timestamp": int(time.time() * 1000)},
        "associations": [{"to": {"id": contact_id},
                           "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}],
    }
    r = requests.post(f"{BASE}/crm/v3/objects/notes", headers=HEADERS, json=body, timeout=30)
    return r.status_code < 400


def write_score_properties(contact_id, score, tier, assigned_rep, last_processed_prop):
    """Writes Lead Score, Outreach Tier, Assigned Rep, and stamps Last Processed Date (today)."""
    import datetime
    properties = {"lead_score": score, "outreach_tier": tier, "assigned_rep": assigned_rep}
    if last_processed_prop:
        # HubSpot date-only properties expect midnight UTC as epoch milliseconds
        today_midnight_utc = datetime.datetime.combine(
            datetime.date.today(), datetime.time.min, tzinfo=datetime.timezone.utc
        )
        properties[last_processed_prop] = int(today_midnight_utc.timestamp() * 1000)
    body = {"properties": properties}
    r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{contact_id}", headers=HEADERS, json=body, timeout=30)
    return r.status_code < 400, r.text if r.status_code >= 400 else None


def stage_route_by_tier(scored, source_event_prop, last_processed_prop,
                         touch_number_prop=None, last_touch_date_prop=None, deal_context_prop=None):
    print("=" * 70)
    print("STAGE 5: Routing by tier")
    print("=" * 70)

    # Skip anyone already processed today -- this is what makes re-running
    # the funnel multiple times a day safe (no duplicate drafts/notes)
    replied = [e for e in scored if e["tier"] == "replied"]
    for e in replied:
        print(f"  {e['props'].get('firstname','')} {e['props'].get('lastname','')}: "
              f"already replied (score {e['score']}) -- no automated action, needs human follow-up")

    to_process = [e for e in scored if not e.get("processed_today") and e["tier"] != "replied"]
    skipped = len(scored) - len(to_process)
    if skipped:
        print(f"\nSkipping {skipped} contact(s) already processed today.")

    low = [e for e in to_process if e["tier"] == "low"]
    mid = [e for e in to_process if e["tier"] == "mid"]
    top_all = [e for e in to_process if e["tier"] == "top"]
    top = sorted(top_all, key=lambda e: e["score"], reverse=True)[:MAX_DRAFTS_PER_RUN]
    if len(top_all) > MAX_DRAFTS_PER_RUN:
        print(f"\n(Note: {len(top_all)} contacts are Top tier, but capping Gmail drafts at "
              f"{MAX_DRAFTS_PER_RUN} per run -- highest scorers first)")

    # Write score + tier + assigned rep + last-processed-date on everyone we're processing
    print("\nWriting Lead Score + Outreach Tier + Assigned Rep properties...")
    prop_errors = 0
    for e in to_process:
        if e["tier"] == "top":
            rep_label = SALESPEOPLE.get(e["owner"], "Unassigned - Needs Review").split(" (")[0]
        elif e["tier"] == "mid":
            rep_label = "Automated Sequence"
        else:
            rep_label = "Automated Nurture"
        ok, err = write_score_properties(e["contact_id"], e["score"], e["tier"].capitalize(),
                                          rep_label, last_processed_prop)
        if not ok:
            prop_errors += 1
    if prop_errors:
        print(f"  {len(to_process) - prop_errors}/{len(to_process)} succeeded ({prop_errors} failed)")
    else:
        print(f"  Done for all {len(to_process)} contacts.")

    print(f"\nLOW tier ({len(low)}) -> tagging for automated nurture (no Gmail draft, email nurture only)")
    for e in low:
        note = (f"Score: {e['score']} (Low tier). "
                f"Routed to: no salesperson -- automated email nurture only. "
                f"Next step: enrolled in automated email nurture sequence, no human action needed.")
        log_note(e["contact_id"], note)
    print(f"  Tagged {len(low)} contact(s).")

    print(f"\nMID tier ({len(mid)}) -> generic automated Gmail draft (Touch 1 of sequence)")
    try:
        from gmail_client import get_gmail_service, create_draft
        service = get_gmail_service()
        mid_gmail_ok = True
    except Exception as e:
        print(f"  Gmail not available: {e}")
        mid_gmail_ok = False

    if mid_gmail_ok:
        for e in mid:
            p = e["props"]
            to_email = p.get("email")
            if not to_email:
                print(f"  SKIP {p.get('firstname','')} {p.get('lastname','')} — no email")
                log_note(e["contact_id"], f"Score: {e['score']} (Mid tier). Routed to: Automated Sequence. "
                                            f"Next step: BLOCKED -- no email on file.")
                continue
            if not is_valid_email(to_email):
                print(f"  SKIP {p.get('firstname','')} {p.get('lastname','')} — malformed email ({to_email})")
                log_note(e["contact_id"], f"Score: {e['score']} (Mid tier). "
                                            f"Next step: BLOCKED -- email '{to_email}' is malformed, needs manual fix.")
                continue
            subject = f"Following up from {p.get(source_event_prop, 'the event')}"
            body = draft_mid_tier(p, source_event_prop) + f"\n\n---\nDEMO DRAFT for fake/test contact ({p.get('firstname')} {p.get('lastname')})."
            create_draft(service, to_email, subject, body)
            print(f"  Draft created (generic template) for {p.get('firstname','')} {p.get('lastname','')}")
            log_note(e["contact_id"], f"Score: {e['score']} (Mid tier). Routed to: Automated Sequence (no specific rep). "
                                        f"Next step: generic Gmail draft auto-created (Touch 1), pending human review and send.")

    print(f"\nTOP tier ({len(top)}) -> personalized Gmail draft + routed to salesperson")
    try:
        from gmail_client import get_gmail_service, create_draft
        service = get_gmail_service()
    except Exception as e:
        print(f"  Gmail not available: {e}")
        return

    for e in top:
        p = e["props"]
        to_email = p.get("email")
        owner_key = e["owner"]
        owner_label = SALESPEOPLE.get(owner_key, "UNASSIGNED -- no country on file, needs manual review")

        if not to_email:
            print(f"  SKIP {p.get('firstname','')} {p.get('lastname','')} — no email")
            note = (f"Score: {e['score']} (Top tier). Routed to: {owner_label}. "
                    f"Next step: BLOCKED -- no email on file, cannot draft outreach. Needs manual lookup.")
            log_note(e["contact_id"], note)
            continue

        if not is_valid_email(to_email):
            print(f"  SKIP {p.get('firstname','')} {p.get('lastname','')} — malformed email ({to_email})")
            note = (f"Score: {e['score']} (Top tier). Routed to: {owner_label}. "
                    f"Next step: BLOCKED -- email '{to_email}' is malformed, needs manual fix.")
            log_note(e["contact_id"], note)
            continue

        # Determine which touch of the nurture campaign is due
        current_touch = 0
        if touch_number_prop:
            current_touch = int(p.get(touch_number_prop) or 0)
        if current_touch >= 3:
            print(f"  {p.get('firstname','')} {p.get('lastname','')}: nurture campaign complete (3/3 touches sent)")
            continue

        next_touch = current_touch + 1
        required_gap = NURTURE_TOUCH_GAP_DAYS[next_touch]
        if next_touch > 1 and last_touch_date_prop:
            days_since = None
            raw = p.get(last_touch_date_prop)
            if raw:
                import datetime as _dt
                if isinstance(raw, str) and not raw.isdigit():
                    dt = _dt.datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
                else:
                    dt = _dt.datetime.fromtimestamp(int(raw) / 1000, tz=_dt.timezone.utc)
                days_since = (_dt.datetime.now(tz=_dt.timezone.utc) - dt).days
            if days_since is None or days_since < required_gap:
                print(f"  {p.get('firstname','')} {p.get('lastname','')}: touch {next_touch} not due yet "
                      f"({days_since} days since last touch, needs {required_gap})")
                continue

        use_case, bottleneck = (None, None)
        if deal_context_prop:
            use_case, bottleneck = parse_deal_context(p.get(deal_context_prop))

        subject = f"Following up on {use_case[:40] if use_case else 'our conversation'}" if next_touch > 1 \
            else f"Great connecting at {p.get(source_event_prop, 'the event')}"
        body = draft_nurture_touch(p, next_touch, use_case, bottleneck) + \
            f"\n\n---\nDEMO DRAFT for fake/test contact ({p.get('firstname')} {p.get('lastname')}). Nurture touch {next_touch}/3."
        create_draft(service, to_email, subject, body)
        print(f"  Nurture touch {next_touch}/3 created for {p.get('firstname','')} {p.get('lastname','')} "
              f"-> routed to {owner_key or 'unassigned'}")

        if touch_number_prop and last_touch_date_prop:
            import datetime as _dt2
            today_ms = int(_dt2.datetime.combine(_dt2.date.today(), _dt2.time.min,
                                                  tzinfo=_dt2.timezone.utc).timestamp() * 1000)
            requests.patch(f"{BASE}/crm/v3/objects/contacts/{e['contact_id']}", headers=HEADERS,
                           json={"properties": {touch_number_prop: next_touch, last_touch_date_prop: today_ms}},
                           timeout=30)

        context_note = f" Bottleneck addressed: {bottleneck}." if bottleneck else ""
        note = (f"Score: {e['score']} (Top tier). Routed to: {owner_label}. "
                f"Next step: nurture campaign touch {next_touch}/3 auto-created, pending human review and send.{context_note}")
        log_note(e["contact_id"], note)

        note = (f"Score: {e['score']} (Top tier). Routed to: {owner_label}. "
                f"Next step: personalized Gmail draft auto-created, pending human review and send.")
        log_note(e["contact_id"], note)


# ==================== RUN ====================
if __name__ == "__main__":
    enriched = stage_prospect_and_enrich()
    if enriched:
        stage_import_to_hubspot(enriched)

    props_map = find_property_names()
    source_event_prop = props_map["source_event"]
    employee_count_prop = props_map["employee_count"]
    last_processed_prop = props_map["last_processed"]
    touch_number_prop = props_map["touch_number"]
    last_touch_date_prop = props_map["last_touch_date"]
    deal_context_prop = props_map["deal_context"]

    if not source_event_prop:
        print("Couldn't find 'Source Event' property.")
        sys.exit(1)
    if not last_processed_prop:
        print("WARNING: 'Last Processed Date' property not found -- duplicate-prevention will be "
              "skipped this run. Create it (Date picker type) to enable safe re-runs.")
    if not deal_context_prop:
        print("WARNING: 'Deal Context' property not found -- Top-tier nurture campaigns will use "
              "generic fallback messaging instead of bottleneck-specific content.")

    scored = stage_score_everyone(source_event_prop, employee_count_prop, last_processed_prop,
                                   touch_number_prop, last_touch_date_prop, deal_context_prop)
    stage_route_by_tier(scored, source_event_prop, last_processed_prop,
                         touch_number_prop, last_touch_date_prop, deal_context_prop)

    print("\n" + "=" * 70)
    print("FUNNEL COMPLETE")
    print("=" * 70)
