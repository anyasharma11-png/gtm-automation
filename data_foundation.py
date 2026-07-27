"""
data_foundation.py

Harvests real context beyond HubSpot's own contact/deal properties:
  1. Slack channel history -- fuzzy-matched to each deal's company name
  2. HubSpot's native Meetings/Calls engagements -- already-logged activity
     most people don't realize is queryable via API

This is intentionally keyword/fuzzy-match based, not AI-summarized --
same accuracy tradeoff the GTM template itself acknowledges for its own
signal detection. Matches are flagged with confidence; unmatched accounts
are reported explicitly rather than silently treated as "no activity."

Nothing here creates new HubSpot properties -- results get folded directly
into the AI Coaching Brief text that deal_coaching.py already generates.
"""

import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

DEMO_KEYWORDS = ["demo", "demoed", "showed", "walkthrough", "walked through", "presentation"]

# Curated signal lists -- same regex/keyword approach the GTM template itself
# uses (not model-based), scanned across all harvested text for a deal.
BUYING_SIGNALS = [
    "pilot", "budget approved", "signed", "moving forward", "next steps",
    "committee approved", "ready to proceed", "green light", "purchase order",
    "contract", "timeline confirmed", "loved it", "excited to move forward",
    "scheduling implementation", "sounds great", "let's do it", "when can we start",
    "demo went well", "great demo", "loved the demo", "impressed with the demo",
    "went well", "positive feedback",
]
URGENCY_SIGNALS = ["asap", "urgent", "this quarter", "need this by", "fast track", "expedite"]
RISK_SIGNALS = [
    "not now", "no budget", "on hold", "competitor", "another vendor",
    "not a priority", "no response", "ghosted", "budget cut", "reorg",
    "postponed", "delayed", "concerns about", "hesitant", "pushback",
    "not interested", "pausing", "going with", "budget freeze",
]


def fetch_contact_notes_text(contact_ids):
    """Pulls the text of every Note logged on a set of contacts (includes
    our own system notes -- reply detections, nurture touches -- plus any
    manual notes a rep adds)."""
    all_text = []
    for cid in contact_ids:
        r = requests.get(f"{BASE}/crm/v4/objects/contact/{cid}/associations/note", headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            continue
        note_ids = [res["toObjectId"] for res in r.json().get("results", [])][:10]
        for nid in note_ids:
            nr = requests.get(f"{BASE}/crm/v3/objects/notes/{nid}", headers=HEADERS,
                               params={"properties": "hs_note_body"}, timeout=30)
            if nr.status_code < 400:
                body = nr.json().get("properties", {}).get("hs_note_body", "")
                if body:
                    all_text.append(body)
    return all_text


def fetch_raw_slack_text(company, slack_channels):
    match = match_channel_to_company(slack_channels, company)
    if not match:
        return []
    messages = fetch_channel_messages(match["id"])
    if not messages:
        return []
    return [m.get("text", "") for m in messages]


def detect_deal_health(deal_id, contact_ids, company, slack_channels):
    """
    Scans ALL harvested text for a deal (HubSpot Notes + Slack + meeting/call
    bodies) for curated buying/urgency/risk signal phrases. Risk signals
    override -- one clear risk phrase makes the deal Red regardless of other
    positive signals, since a real objection matters more than general
    positive tone. Returns (health, matched_signals_list).
    """
    all_text = []
    all_text.extend(fetch_contact_notes_text(contact_ids))
    all_text.extend(fetch_raw_slack_text(company, slack_channels))

    for obj_type in ["meetings", "calls"]:
        r = requests.get(f"{BASE}/crm/v4/objects/deal/{deal_id}/associations/{obj_type}",
                          headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            continue
        ids = [res["toObjectId"] for res in r.json().get("results", [])][:5]
        for oid in ids:
            prop = "hs_meeting_body" if obj_type == "meetings" else "hs_call_body"
            or_r = requests.get(f"{BASE}/crm/v3/objects/{obj_type}/{oid}", headers=HEADERS,
                                 params={"properties": prop}, timeout=30)
            if or_r.status_code < 400:
                body = or_r.json().get("properties", {}).get(prop, "")
                if body:
                    all_text.append(body)

    combined = " ".join(all_text).lower()
    if not combined.strip():
        return "Neutral", []

    matched_risk = [s for s in RISK_SIGNALS if s in combined]
    matched_buying = [s for s in BUYING_SIGNALS if s in combined]
    matched_urgency = [s for s in URGENCY_SIGNALS if s in combined]

    if matched_risk:
        return "Red", matched_risk
    if matched_buying or matched_urgency:
        return "Green", matched_buying + matched_urgency
    return "Yellow", []


# ==================== SLACK ====================
def list_slack_channels():
    """Returns list of {id, name} for channels the bot can see."""
    if not SLACK_TOKEN:
        return []
    r = requests.get("https://slack.com/api/conversations.list",
                      headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
                      params={"types": "public_channel", "limit": 200}, timeout=30)
    data = r.json()
    if not data.get("ok"):
        print(f"  Slack channel list failed: {data.get('error')}")
        return []
    return [{"id": c["id"], "name": c["name"]} for c in data.get("channels", [])]


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match_channel_to_company(channels, company):
    """
    Fuzzy match: does the channel name contain (or get contained by) a
    normalized version of the company name? Flags confidence honestly --
    this is exactly the kind of fuzzy company-name matching the GTM
    template itself warns produces false positives, so we keep it simple
    and conservative (substring match only, no aggressive fuzzy scoring).
    """
    company_norm = normalize(company.split(" ")[0])  # first word only, e.g. "Nordstrand"
    if len(company_norm) < 4:
        return None  # too short to match safely, avoid false positives
    for ch in channels:
        ch_norm = normalize(ch["name"])
        if company_norm in ch_norm or ch_norm in company_norm:
            return ch
    return None


def fetch_channel_messages(channel_id, limit=20):
    if not SLACK_TOKEN:
        return []
    r = requests.get("https://slack.com/api/conversations.history",
                      headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
                      params={"channel": channel_id, "limit": limit}, timeout=30)
    data = r.json()
    if not data.get("ok"):
        return None  # bot likely not in this channel, or missing scope
    return data.get("messages", [])


def summarize_slack_activity(company, channels):
    match = match_channel_to_company(channels, company)
    if not match:
        return None, "No matching Slack channel found for this account."

    messages = fetch_channel_messages(match["id"])
    if messages is None:
        return match["name"], (f"Matched channel #{match['name']}, but couldn't read history "
                                f"(bot may need to be invited, or missing channels:history scope).")
    if not messages:
        return match["name"], f"Matched channel #{match['name']}, but no messages found."

    demo_mentioned = any(any(k in (m.get("text", "").lower()) for k in DEMO_KEYWORDS) for m in messages)
    caveat = " (Note: this channel match is based on fuzzy company-name matching and could be a false positive -- verify manually before treating as confirmed activity.)"
    return match["name"], (f"{len(messages)} recent message(s) in #{match['name']}. "
                            f"{'A demo/walkthrough was mentioned in chat history.' if demo_mentioned else 'No demo mentioned in recent chat history.'}"
                            f"{caveat}")


# ==================== HUBSPOT NATIVE MEETINGS/CALLS ====================
def fetch_deal_meetings_and_calls(deal_id):
    """Pulls HubSpot's native Meeting and Call engagements associated with a deal."""
    summaries = []
    for obj_type in ["meetings", "calls"]:
        r = requests.get(f"{BASE}/crm/v4/objects/deal/{deal_id}/associations/{obj_type}",
                          headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            continue
        ids = [res["toObjectId"] for res in r.json().get("results", [])]
        for oid in ids[:5]:  # cap to avoid excessive calls
            prop = "hs_meeting_title,hs_meeting_body" if obj_type == "meetings" else "hs_call_title,hs_call_body"
            or_r = requests.get(f"{BASE}/crm/v3/objects/{obj_type}/{oid}", headers=HEADERS,
                                 params={"properties": prop}, timeout=30)
            if or_r.status_code < 400:
                p = or_r.json().get("properties", {})
                title = p.get("hs_meeting_title") or p.get("hs_call_title") or f"Untitled {obj_type[:-1]}"
                summaries.append(f"{obj_type[:-1].capitalize()}: {title}")
    return summaries


# ==================== MAIN ====================
def gather_account_context(company, deal_id, slack_channels):
    """Returns (meeting_history_text, solutions_demoed_text) for one account."""
    channel_name, slack_summary = summarize_slack_activity(company, slack_channels)
    native_events = fetch_deal_meetings_and_calls(deal_id)

    if native_events:
        meeting_history = f"{len(native_events)} logged HubSpot meeting/call(s): " + "; ".join(native_events)
    else:
        meeting_history = "No HubSpot meetings/calls logged for this deal."

    meeting_history += f" | Slack: {slack_summary}"

    demo_signal = any("demo" in e.lower() or "walkthrough" in e.lower() for e in native_events)
    demo_signal = demo_signal or (slack_summary and "demo" in slack_summary.lower() and "mentioned" in slack_summary.lower() and "No demo" not in slack_summary)
    solutions_demoed = "A demo/walkthrough appears to have occurred (per logged meetings or chat mentions)." \
        if demo_signal else "No demos logged yet."

    return meeting_history, solutions_demoed
