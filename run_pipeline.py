"""
run_pipeline.py  (v3)

Pulls contacts from live HubSpot, scores, routes, drafts — then:
  1. Generates one HTML queue page per salesperson (email-only now, no LinkedIn)
  2. Creates real Gmail drafts for Top-tier contacts
  3. Logs a Note back onto each contact's HubSpot record so HubSpot and
     Gmail stay in sync (HubSpot shows a draft was created, even though
     nothing has actually been sent yet — sending is still a human step)

Run: python3 run_pipeline.py
Output: queues/queue_<name>.html
"""

import os
import re
import sys
import html
import urllib.parse
from datetime import date

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("Missing packages. Run:  python3 -m pip install requests python-dotenv")
    sys.exit(1)

load_dotenv()
KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
if not KEY:
    print("No key found in .env")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

# ---------- Property auto-detection ----------
def find_property_names():
    r = requests.get(f"{BASE}/crm/v3/properties/contacts", headers=HEADERS, timeout=30)
    r.raise_for_status()
    props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
    source_event = employee_count = None
    for name, label in props.items():
        norm = label.strip().lower()
        if norm == "source event":
            source_event = name
        if norm == "employee count":
            employee_count = name
    return source_event, employee_count

SOURCE_EVENT_PROP, EMPLOYEE_COUNT_PROP = find_property_names()
if not SOURCE_EVENT_PROP:
    print("Couldn't find 'Source Event' property.")
    sys.exit(1)

# ---------- Pull ----------
def fetch_contacts():
    contacts, after = [], None
    props = ["firstname", "lastname", "email", "jobtitle", "company", "industry", "country", SOURCE_EVENT_PROP]
    if EMPLOYEE_COUNT_PROP:
        props.append(EMPLOYEE_COUNT_PROP)
    while True:
        body = {"filterGroups": [{"filters": [{"propertyName": SOURCE_EVENT_PROP, "operator": "HAS_PROPERTY"}]}],
                "properties": props, "limit": 100}
        if after:
            body["after"] = after
        r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        contacts.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            return contacts

# ---------- Scoring (word-boundary title matching — fixes the "postdoCTOral" bug) ----------
SENIOR_TITLES = ["cso", "chief scientific officer", "chief medical officer", "chief development officer",
                 "chief innovation officer", "vp", "v.p.", "vice president", "director", "cto", "cio", "head of"]
MID_TITLES = ["manager", "architect", "lead", "principal"]
TARGET_SECTORS = ["pharmaceutical", "biotech", "research"]

def title_points(title):
    t = (title or "").lower()
    for k in SENIOR_TITLES:
        if re.search(r"\b" + re.escape(k) + r"\b", t):
            return 30, "Senior title"
    for k in MID_TITLES:
        if re.search(r"\b" + re.escape(k) + r"\b", t):
            return 20, "Mid-level title"
    return 0, None

def safe_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

def score_contact(p):
    breakdown = []
    pts, label = title_points(p.get("jobtitle"))
    if pts:
        breakdown.append((label, pts))
    if EMPLOYEE_COUNT_PROP and safe_int(p.get(EMPLOYEE_COUNT_PROP)) >= 1000:
        breakdown.append(("Enterprise company (1000+)", 20))
    if (p.get("industry") or "").lower() in TARGET_SECTORS:
        breakdown.append(("Target sector", 15))
    return sum(x for _, x in breakdown), breakdown

def get_tier(score):
    return "top" if score >= 65 else "mid" if score >= 35 else "low"

# ---------- Routing ----------
APAC = ["india", "singapore", "japan", "australia", "new zealand", "china", "south korea",
        "malaysia", "thailand", "indonesia", "philippines", "vietnam"]
AMERICAS = ["united states", "usa", "canada", "mexico", "brazil", "argentina", "chile", "colombia"]

SALESPEOPLE = {
    "prateek": {"name": "Prateek", "region": "US / Americas"},
    "carole": {"name": "Carole", "region": "Europe / Other International"},
    "shiwam": {"name": "Shiwam", "region": "India / APAC"},
}

def route(country):
    c = (country or "").strip().lower()
    if not c:
        return None
    if c in APAC:
        return "shiwam"
    if c in AMERICAS:
        return "prateek"
    return "carole"

# ---------- Drafts ----------
BUYER_KEYWORDS = ["cso", "chief", "vp", "v.p.", "vice president", "director", "cto", "cio", "head of"]

def is_buyer(title):
    t = (title or "").lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", t) for k in BUYER_KEYWORDS)

def draft(p):
    first = p.get("firstname") or "there"
    company = p.get("company") or "your team"
    event = p.get(SOURCE_EVENT_PROP) or "the event"
    if is_buyer(p.get("jobtitle")):
        return (f"Hi {first}, great connecting around {event}. At [YourCompany] we help teams like "
                f"{company}'s streamline [core value prop] without adding operational overhead. "
                f"No ask here — just glad to be connected.")
    return (f"Hi {first}, enjoyed seeing {company}'s work come up around {event}. We work on "
            f"[technical value prop] and thought it'd be good to connect with people hands-on in this space.")

# ---------- HTML generation ----------
TIER_BADGE = {
    "top": ("Top", "#166534", "#DCFCE7"),
    "mid": ("Mid", "#92400E", "#FEF3C7"),
}

def render_queue_html(owner_key, entries):
    person = SALESPEOPLE[owner_key]
    today = date.today().strftime("%B %d, %Y")
    cards = []
    for i, e in enumerate(entries):
        p = e["props"]
        name = html.escape(f"{p.get('firstname','')} {p.get('lastname','')}".strip())
        title_co = html.escape(f"{p.get('jobtitle','')} at {p.get('company','')}")
        country = html.escape(p.get("country") or "")
        event = html.escape(p.get(SOURCE_EVENT_PROP) or "")
        label, fg, bg = TIER_BADGE[e["tier"]]
        msg = draft(p)
        msg_html = html.escape(msg)
        breakdown_str = " · ".join(f"{lbl} +{pts}" for lbl, pts in e["breakdown"]) or "no signals"
        gmail_note = " · Gmail draft auto-created" if e["tier"] == "top" else ""
        cards.append(f"""
        <div class="card">
          <div class="row">
            <div>
              <div class="name">{name} <span class="badge" style="color:{fg};background:{bg}">{label} · {e['score']}</span></div>
              <div class="meta">{title_co} · {country} · {html.escape(p.get('email') or 'no email')}</div>
              <div class="signals">{html.escape(breakdown_str)} · {event}{gmail_note}</div>
            </div>
          </div>
          <div class="draft" id="draft-{i}">{msg_html}</div>
          <button class="btn copy" onclick="copyDraft({i}, this)">Copy message</button>
        </div>""")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Email Queue — {person['name']}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 24px auto; padding: 0 16px; color: #1E293B; }}
  h1 {{ font-size: 22px; margin-bottom: 2px; }}
  .sub {{ color: #64748B; font-size: 14px; margin-bottom: 20px; }}
  .card {{ border: 1px solid #E2E8F0; border-radius: 10px; padding: 16px; margin-bottom: 14px; }}
  .row {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
  .name {{ font-weight: 600; font-size: 16px; }}
  .badge {{ font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 5px; margin-left: 6px; }}
  .meta {{ color: #475569; font-size: 14px; margin-top: 2px; }}
  .signals {{ color: #94A3B8; font-size: 12px; margin-top: 4px; }}
  .draft {{ background: #F8FAFC; border: 1px dashed #CBD5E1; border-radius: 8px; padding: 10px 12px; font-size: 14px; line-height: 1.5; margin: 12px 0 8px; }}
  .btn {{ display: inline-block; border: none; border-radius: 7px; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; text-decoration: none; }}
  .copy {{ background: #E2E8F0; color: #1E293B; }}
  .copy.done {{ background: #DCFCE7; color: #166534; }}
  .note {{ background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #92400E; margin-bottom: 20px; }}
</style></head><body>
<h1>Email Queue — {person['name']}</h1>
<div class="sub">{person['region']} · {len(entries)} contacts · generated {today}</div>
<div class="note"><strong>How to use:</strong> Top-tier contacts already have a real Gmail draft waiting in Drafts — just review and send. Mid-tier contacts: copy the message below and send manually.</div>
{"".join(cards)}
<script>
function copyDraft(i, btn) {{
  const text = document.getElementById('draft-' + i).innerText;
  navigator.clipboard.writeText(text).then(() => {{
    btn.textContent = 'Copied ✓'; btn.classList.add('done');
    setTimeout(() => {{ btn.textContent = 'Copy message'; btn.classList.remove('done'); }}, 2000);
  }});
}}
</script>
</body></html>"""

# ---------- Run ----------
print("Pulling contacts from HubSpot...")
raw = fetch_contacts()
print(f"Found {len(raw)} contacts with a Source Event.\n")

queues = {"prateek": [], "carole": [], "shiwam": [], "review": []}
for c in raw:
    p = c["properties"]
    score, breakdown = score_contact(p)
    tier = get_tier(score)
    owner = route(p.get("country"))
    entry = {"contact_id": c["id"], "props": p, "score": score, "tier": tier, "breakdown": breakdown}
    if owner is None:
        queues["review"].append(entry)
    elif tier != "low":
        queues[owner].append(entry)

os.makedirs("queues", exist_ok=True)
for key, person in SALESPEOPLE.items():
    q = sorted(queues[key], key=lambda e: e["score"], reverse=True)
    path = os.path.join("queues", f"queue_{key}.html")
    with open(path, "w") as f:
        f.write(render_queue_html(key, q))
    print(f"  {person['name']:8s} — {len(q):2d} contacts -> {path}")

print(f"\nManual review needed (no country): {len(queues['review'])}")
for e in queues["review"]:
    p = e["props"]
    print(f"  - {p.get('firstname','')} {p.get('lastname','')} ({p.get('jobtitle')})")

total_queued = sum(len(queues[k]) for k in SALESPEOPLE)
print(f"\nSummary: {len(raw)} pulled · {total_queued} queued · {len(raw) - total_queued - len(queues['review'])} low-tier · {len(queues['review'])} review")
print("Done. Open the queues/ folder and double-click any HTML file to see it.")

# ---------- Gmail drafts for Top-tier contacts ----------
DRAFT_CAP = 10  # safety cap so a run never floods your Drafts folder

top_tier_entries = []
for key in SALESPEOPLE:
    top_tier_entries.extend([e for e in queues[key] if e["tier"] == "top"])
top_tier_entries = sorted(top_tier_entries, key=lambda e: e["score"], reverse=True)[:DRAFT_CAP]

# ---------- HubSpot write-back: log a Note after each Gmail draft ----------
def log_draft_note(contact_id, subject, to_email):
    """
    Logs a Note on the contact's HubSpot record so HubSpot reflects that a
    Gmail draft exists, even though it hasn't been sent yet. This keeps
    HubSpot and Gmail in sync without falsely claiming an email was sent.
    Requires crm.objects.contacts.write scope on the Service Key.
    """
    note_body = (f"Gmail draft auto-created by lead activation pipeline.<br>"
                 f"Subject: {subject}<br>To: {to_email}<br>"
                 f"Status: sitting in Drafts, pending human review and send.")
    body = {
        "properties": {"hs_note_body": note_body, "hs_timestamp": int(__import__("time").time() * 1000)},
        "associations": [{
            "to": {"id": contact_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]
        }],
    }
    r = requests.post(f"{BASE}/crm/v3/objects/notes", headers=HEADERS, json=body, timeout=30)
    if r.status_code == 403:
        return False, "missing scope (add crm.objects.contacts.write to your Service Key)"
    r.raise_for_status()
    return True, None

if top_tier_entries:
    print(f"\nCreating real Gmail drafts for top {len(top_tier_entries)} contact(s) (capped at {DRAFT_CAP})...")
    try:
        from gmail_client import get_gmail_service, create_draft
        service = get_gmail_service()
        for e in top_tier_entries:
            p = e["props"]
            to_email = p.get("email")
            if not to_email:
                print(f"  SKIP {p.get('firstname','')} {p.get('lastname','')} — no email on file")
                continue
            subject = f"Great connecting at {p.get(SOURCE_EVENT_PROP, 'the event')}"
            body = draft(p) + f"\n\n---\nDEMO DRAFT — auto-generated for fake test contact ({p.get('firstname')} {p.get('lastname')}, {p.get('company')}). Safe to delete."
            draft_id = create_draft(service, to_email, subject, body)
            print(f"  Created draft {draft_id} for {p.get('firstname','')} {p.get('lastname','')} ({to_email})")

            ok, err = log_draft_note(e["contact_id"], subject, to_email)
            if ok:
                print(f"    -> logged to HubSpot (Note on contact {e['contact_id']})")
            else:
                print(f"    -> NOT logged to HubSpot: {err}")
    except FileNotFoundError as ex:
        print(f"  Gmail not set up yet: {ex}")
    except ImportError:
        print("  Missing Gmail packages. Run: python3 -m pip install google-auth google-auth-oauthlib google-api-python-client")
else:
    print("\nNo top-tier contacts to draft emails for.")
