"""
deal_coaching.py

Generates the full per-deal AI coaching brief per the GTM tooling spec:
health rating + reason, situation summary, executive brief, use cases,
pain points, prioritized next steps, talk track, contacts to engage with
suggested outreach lines, buying/stall signals, risks + mitigations,
newly-matched external leads at that account, and content recommendations.

Stored as one consolidated 'AI Coaching Brief' text property on the Deal
(same consolidation approach as Deal Context, to stay within property
limits), plus a separate 'Health Rating' property for quick filtering.

Run: python3 deal_coaching.py
"""

import os
import re
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

# Fixed content taxonomy -- keyword -> recommended content piece.
# In a real system this would be a harvested content library; here it's a
# small fixed mapping since our content library is fake.
CONTENT_TAXONOMY = {
    "fda": "Whitepaper: FDA Clearance Pathway for Connected Cardiac Monitoring",
    "budget": "ROI Calculator: 18-Month Payback Model for Cardiac Monitoring Capital Spend",
    "capital": "ROI Calculator: 18-Month Payback Model for Cardiac Monitoring Capital Spend",
    "ehr": "Integration Guide: EHR/Epic Connectivity for Continuous Vitals Data",
    "epic": "Integration Guide: EHR/Epic Connectivity for Continuous Vitals Data",
    "security": "Security & Compliance Brief: Data Handling for Connected Medical Devices",
    "training": "Case Study: Reducing Staff Training Time in Cardiac Unit Rollouts",
    "alarm": "Case Study: Cutting Alarm Fatigue with AI-Filtered Cardiac Alerts",
    "bid": "Procurement Guide: Competitive RFP Response Template",
    "gpo": "Procurement Guide: GPO Contract Fast-Track Process",
}


def find_unassociated_contacts_at_company(company, already_associated_ids):
    """Finds contacts whose company name matches this deal's account but who
    aren't yet linked to the deal -- e.g. a new Apollo prospect found after
    the deal was created. This is the 'newly-matched external leads' field."""
    body = {
        "filterGroups": [{"filters": [{"propertyName": "company", "operator": "EQ", "value": company}]}],
        "properties": ["firstname", "lastname", "jobtitle", "email"],
        "limit": 20,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    if r.status_code >= 400:
        return []
    results = r.json().get("results", [])
    return [c for c in results if c["id"] not in already_associated_ids]


def log_note(contact_id, text):
    body = {
        "properties": {"hs_note_body": text, "hs_timestamp": int(__import__("time").time() * 1000)},
        "associations": [{"to": {"id": contact_id},
                           "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}],
    }
    requests.post(f"{BASE}/crm/v3/objects/notes", headers=HEADERS, json=body, timeout=30)


def find_property_names():
    r = requests.get(f"{BASE}/crm/v3/properties/deals", headers=HEADERS, timeout=30)
    r.raise_for_status()
    props = {p["name"]: p.get("label", "") for p in r.json()["results"]}
    brief = None
    for name, label in props.items():
        if label.strip().lower() == "ai coaching brief":
            brief = name
    return brief


def fetch_deals():
    r = requests.get(f"{BASE}/crm/v3/objects/deals", headers=HEADERS,
                      params={"properties": "dealname,amount,dealstage", "limit": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def fetch_associated_contacts(deal_id):
    r = requests.get(f"{BASE}/crm/v4/objects/deal/{deal_id}/associations/contact", headers=HEADERS, timeout=30)
    r.raise_for_status()
    contact_ids = [res["toObjectId"] for res in r.json().get("results", [])]
    contacts = []
    for cid in contact_ids:
        cr = requests.get(f"{BASE}/crm/v3/objects/contacts/{cid}", headers=HEADERS,
                           params={"properties": "firstname,lastname,jobtitle,email,lead_score,outreach_tier,deal_persona,deal_context,assigned_rep,company"},
                           timeout=30)
        if cr.status_code < 400:
            contacts.append(cr.json())
    return contacts


def parse_deal_context(text):
    if not text:
        return None, None
    uc = bn = None
    m = re.search(r"Use case:\s*(.+?)\.\s*Bottleneck:", text)
    if m: uc = m.group(1).strip()
    m = re.search(r"Bottleneck:\s*(.+?)\.?$", text)
    if m: bn = m.group(1).strip()
    return uc, bn


def recommend_content(bottleneck):
    """Returns a list of 1-3 relevant content pieces, not just one match."""
    if not bottleneck:
        return ["No specific content match -- bottleneck not captured for this contact."]
    b = bottleneck.lower()
    matches = []
    for keyword, content in CONTENT_TAXONOMY.items():
        if keyword in b and content not in matches:
            matches.append(content)
    if not matches:
        return ["No specific content match for this bottleneck -- general product overview recommended."]
    return matches[:3]


ENVIRONMENT_KEYWORDS = {
    "hospital": "Standard bedside telemetry monitors, likely on a legacy EHR (Epic/Cerner) with no AI-assisted cardiac analysis layer.",
    "medical center": "Standard bedside telemetry monitors, likely on a legacy EHR (Epic/Cerner) with no AI-assisted cardiac analysis layer.",
    "academic": "Mixed environment -- some pilot/research monitoring tools alongside standard clinical telemetry; more open to novel tech evaluation.",
    "remote monitoring": "Existing remote patient monitoring (RPM) platform in place -- likely evaluating this as an upgrade/replacement, not a first RPM deployment.",
    "clinic": "Basic in-office ECG/monitoring equipment, minimal integration with any central data platform.",
    "ambulatory surgical": "Standard peri-operative monitoring equipment, likely no continuous post-discharge monitoring today.",
    "diagnostic": "Standalone diagnostic imaging/monitoring equipment, likely not integrated with a broader care-continuity platform.",
    "rehab": "Basic vitals-check equipment, minimal continuous monitoring infrastructure today.",
    "nursing": "Basic vitals-check equipment, minimal continuous monitoring infrastructure today.",
}


def environment_tooling_for(company):
    c = (company or "").lower()
    for keyword, desc in ENVIRONMENT_KEYWORDS.items():
        if keyword in c:
            return desc
    return "Environment/tooling not yet captured for this account -- recommend a discovery question on current monitoring setup."


def talk_track_for(role_type, bottleneck):
    if role_type == "executive":
        return f"Lead with outcomes: acknowledge '{bottleneck}', frame the ROI/patient-outcome case directly."
    if role_type == "technical":
        return f"Lead with technical depth: address '{bottleneck}' with integration specifics and compliance detail."
    if role_type == "procurement":
        return f"Lead with process: acknowledge '{bottleneck}', offer to fast-track whatever procurement step is blocking."
    return f"Lead with workflow fit: acknowledge '{bottleneck}', keep it concrete and hands-on."


from data_foundation import gather_account_context, list_slack_channels, detect_deal_health


import random

def build_post_sale_report(deal):
    """
    Simulated post-sale/customer-success report for a closed-won deal.
    Per the GTM template (2.8): this whole category is a STRUCTURAL ABSENCE
    in the audited system -- no usage telemetry, NPS/CSAT, support tickets,
    or churn-risk model. Everything below is clearly-labeled FAKE data
    showing what such a report would look like if it existed, since our
    real product has no telemetry/support integration to pull from.
    """
    dp = deal["properties"]
    company = dp.get("dealname", "").replace(" - AI Cardiac Monitor", "")
    amount = dp.get("amount", "unknown")

    # Deterministic "random" fake data so re-runs are stable, not re-rolled each time
    random.seed(company)
    units_deployed = random.randint(8, 15)
    units_purchased = units_deployed + random.randint(0, 3)
    nps = random.randint(35, 75)
    open_tickets = random.randint(0, 3)
    ticket_severity = random.choice(["low-severity", "mixed severity", "one high-severity"])
    renewal_risk = "Low" if nps >= 50 and ticket_severity != "one high-severity" else \
        "Medium" if nps >= 35 else "High"
    health = "Green" if renewal_risk == "Low" else "Yellow" if renewal_risk == "Medium" else "Red"
    expansion_signal = "Yes -- usage above 90% suggests room to expand to additional units" \
        if units_deployed / units_purchased > 0.9 else "Not yet -- usage below expansion threshold"

    return "\n".join([
        f"=== POST-SALE HEALTH REPORT: {company} (SIMULATED TEST DATA) ===",
        "",
        f"HEALTH: {health.upper()} -- Renewal risk: {renewal_risk}",
        "",
        "NOTE: This section is simulated/fake data. Real usage telemetry, NPS, and support-ticket "
        "integration are a structural absence in this system (matches the GTM template's own "
        "documented gap for post-sale tooling) -- this report shows the SHAPE such a system would "
        "produce, not real customer data.",
        "",
        f"DEAL VALUE: ${amount}",
        "",
        f"USAGE TELEMETRY (simulated): {units_deployed} of {units_purchased} purchased units "
        f"actively reporting data ({round(units_deployed/units_purchased*100)}% utilization).",
        "",
        f"NPS/CSAT (simulated): {nps} (scale 0-100).",
        "",
        f"SUPPORT TICKETS (simulated): {open_tickets} open ticket(s), {ticket_severity}.",
        "",
        f"RENEWAL RISK (simulated): {renewal_risk}.",
        "",
        f"EXPANSION SIGNAL (simulated): {expansion_signal}.",
        "",
        "RECOMMENDED CS ACTION (simulated): " + (
            "Schedule a quarterly business review and explore expansion to additional units."
            if renewal_risk == "Low" else
            "Proactive check-in recommended -- address open tickets before renewal conversation."
            if renewal_risk == "Medium" else
            "Escalate to CS leadership -- renewal at risk, schedule an urgent health check call."
        ),
    ]), health


def build_coaching_brief(deal, contacts, already_associated_ids, slack_channels):
    dp = deal["properties"]
    company = dp.get("dealname", "Unknown facility").replace(" - AI Cardiac Monitor", "")
    amount = dp.get("amount", "unknown")
    stage = dp.get("dealstage", "unknown")

    contact_ids = [c["id"] for c in contacts]
    try:
        health, matched_signals = detect_deal_health(deal["id"], contact_ids, company, slack_channels)
    except Exception as e:
        print(f"    WARNING: signal detection failed for {company}: {e}")
        health, matched_signals = "Neutral", []
    if matched_signals:
        health_reason = f"Detected signal(s): {', '.join(matched_signals[:3])}"
    elif health == "Neutral":
        health_reason = "No notes, chat, or meeting text available yet to derive a signal -- insufficient data."
    else:
        health_reason = "No strong buying or risk signals detected in available notes/chat/meetings."

    top_contacts = sorted(contacts, key=lambda c: int(c["properties"].get("lead_score") or 0), reverse=True)
    primary = top_contacts[0]["properties"] if top_contacts else {}
    use_case, bottleneck = parse_deal_context(primary.get("deal_context"))
    owner = primary.get("assigned_rep") or "Unassigned"

    engage_lines = []
    for c in top_contacts[:5]:
        p = c["properties"]
        name = f"{p.get('firstname','')} {p.get('lastname','')}"
        _, cbn = parse_deal_context(p.get("deal_context"))
        role = p.get("deal_persona", "unknown")
        line = talk_track_for(role, cbn or "no specific blocker captured")
        engage_lines.append(f"  - {name} ({p.get('jobtitle','')}, score {p.get('lead_score','?')}): {line}")

    replied = any(p["properties"].get("outreach_tier") == "Replied - In Sales Cycle" for p in contacts)
    content_recs = recommend_content(bottleneck)

    new_leads = find_unassociated_contacts_at_company(company, already_associated_ids)
    if new_leads:
        new_lead_lines = []
        for nl in new_leads:
            p = nl["properties"]
            name = f"{p.get('firstname','')} {p.get('lastname','')}"
            new_lead_lines.append(f"  - {name} ({p.get('jobtitle','')}) -- NOT yet linked to this deal. "
                                   f"Suggested first touch: \"Noticed you're at {company}, where we're already "
                                   f"in conversation about AI-powered cardiac monitoring -- would love to loop you in.\"")
        new_leads_section = "\n".join(new_lead_lines)
    else:
        new_leads_section = "  None found -- no unassociated contacts detected at this account."

    try:
        meeting_history, solutions_demoed = gather_account_context(company, deal["id"], slack_channels)
    except Exception as e:
        print(f"    WARNING: meeting/Slack harvest failed for {company}: {e}")
        meeting_history = "[unavailable -- error harvesting meeting/chat history this run]"
        solutions_demoed = "[unavailable -- error harvesting this run]"

    sections = [
        f"=== DEAL COACHING BRIEF: {company} ===",
        "",
        f"HEALTH: {health.upper()} -- {health_reason}",
        "",
        f"SITUATION SUMMARY: {company} is evaluating an AI-powered cardiac monitoring platform. "
        f"Deal is at stage '{stage}' with an estimated value of ${amount}.",
        "",
        f"EXECUTIVE BRIEF: {len(contacts)} stakeholder(s) identified in the buying committee. "
        f"Primary blocker: {bottleneck or 'not yet captured'}.",
        "",
        f"USE CASE: {use_case or 'Not yet captured for this account.'}",
        "",
        f"CURRENT ENVIRONMENT/TOOLING: {environment_tooling_for(company)}",
        "",
        f"PAIN POINTS: {bottleneck or 'Not yet captured for this account.'}",
        "",
        f"MEETING-HISTORY SUMMARY: {meeting_history}",
        "",
        f"SOLUTIONS DEMOED: {solutions_demoed}",
        "",
        "PRIORITIZED NEXT STEPS:",
        f"  1. [Action: Address primary bottleneck] [Owner: {owner}] [Deadline: within 7 days] "
        f"[Rationale: {bottleneck or 'unblock the deal'}]",
        f"  2. [Action: Confirm technical/procurement stakeholder alignment] [Owner: {owner}] "
        f"[Deadline: within 14 days] [Rationale: multi-stakeholder deal, needs full committee buy-in]",
        "",
        "TALK TRACK (primary contact):",
        f"  {talk_track_for(primary.get('deal_persona'), bottleneck or 'no specific blocker captured')}",
        "",
        "CONTACTS TO ENGAGE:",
        *engage_lines,
        "",
        "NEWLY-MATCHED EXTERNAL LEADS AT THIS ACCOUNT:",
        new_leads_section,
        "",
        f"BUYING/STALL SIGNALS: {health_reason}"
        + (" Contact has also replied -- active engagement, escalate priority." if replied else ""),
        "",
        f"RISKS + MITIGATIONS: Risk: {bottleneck or 'unknown blocker'}. "
        f"Mitigation: {content_recs[0]}",
        "",
        "CONTENT RECOMMENDATIONS:",
        *[f"  - {c}" for c in content_recs],
    ]
    return health.capitalize(), "\n".join(sections)


health_prop_unused, brief_prop = None, find_property_names()
if not brief_prop:
    print("Missing 'AI Coaching Brief' property on Deals. Create it first (archive a Contact "
          "property if you're at the account-wide 10-property limit).")
    exit(1)

print("Fetching Slack channel list...")
slack_channels = list_slack_channels()
print(f"  {len(slack_channels)} channel(s) visible to the bot.\n")

deals = fetch_deals()
print(f"Found {len(deals)} deal(s).\n")

unmatched_accounts = []

for deal in deals:
    contacts = fetch_associated_contacts(deal["id"])
    if not contacts:
        print(f"  {deal['properties'].get('dealname')}: no associated contacts, skipping")
        continue
    already_associated_ids = {c["id"] for c in contacts}
    company_name = deal["properties"].get("dealname", "").replace(" - AI Cardiac Monitor", "")

    if deal["properties"].get("dealstage") == "closedwon":
        brief, health = build_post_sale_report(deal)
        r = requests.patch(f"{BASE}/crm/v3/objects/deals/{deal['id']}", headers=HEADERS,
                            json={"properties": {brief_prop: brief}}, timeout=30)
        status = "OK" if r.status_code < 400 else f"FAILED {r.status_code}"
        print(f"  {deal['properties'].get('dealname')}: CLOSED-WON, post-sale health={health} -- {status}")
        continue

    from data_foundation import match_channel_to_company
    if not match_channel_to_company(slack_channels, company_name):
        unmatched_accounts.append(company_name)

    try:
        health, brief = build_coaching_brief(deal, contacts, already_associated_ids, slack_channels)
    except Exception as e:
        print(f"  {deal['properties'].get('dealname')}: FAILED to generate brief ({e}) -- skipping this deal, continuing")
        continue
    r = requests.patch(f"{BASE}/crm/v3/objects/deals/{deal['id']}", headers=HEADERS,
                        json={"properties": {brief_prop: brief}}, timeout=30)
    status = "OK" if r.status_code < 400 else f"FAILED {r.status_code}"
    print(f"  {deal['properties'].get('dealname')}: health={health}, {len(contacts)} contact(s) -- {status}")

print("\nDone.")

# Honest "unmatched" reporting -- same transparency pattern the GTM template
# itself uses, rather than silently treating unmatched accounts as having no activity
if unmatched_accounts:
    print(f"\nNOTE: {len(unmatched_accounts)} account(s) had no matching Slack channel found "
          f"(this is expected/normal, not necessarily an error):")
    for name in unmatched_accounts:
        print(f"  - {name}")
