"""
check_replies.py

Checks Gmail for replies to our outreach threads. Any thread with more
than one message means someone replied. For each reply found, updates
the matching HubSpot contact:
  - Outreach Tier property -> "Replied - In Sales Cycle"
  - Logs a Note describing the reply was detected

Run: python3 check_replies.py
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

from gmail_client import get_gmail_service, find_threads_by_subject, thread_has_reply


REPLY_SCORE_BONUS = 30  # a reply is a very strong signal -- meaningful score jump


def find_contact_by_email(email):
    body = {
        "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
        "properties": ["firstname", "lastname", "email", "lead_score", "outreach_tier",
                        "reengagement_touch_number", "assigned_rep", "source_event"],
        "limit": 1,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def describe_attribution(contact_properties):
    """
    Multi-touch attribution: which specific touch was this contact on when
    they replied? We don't have a separate log of every touch sent, but the
    'reengagement_touch_number' property (reused for both the Top-tier
    nurture campaign and the stalled-deal re-engagement motion) tells us
    exactly how many touches had gone out before this reply came in.
    """
    touch_num = contact_properties.get("reengagement_touch_number")
    source = contact_properties.get("source_event", "")
    rep = contact_properties.get("assigned_rep", "Unknown")

    campaign_type = "Re-engagement motion" if source == "Stalled Deal Test" else \
        "Top-tier nurture campaign" if rep and rep != "Automated Nurture" else \
        "Mid-tier automated sequence"

    if touch_num is None or int(touch_num) == 0:
        return f"{campaign_type} -- replied before any tracked touch was sent (or touch tracking unavailable)."
    return f"{campaign_type} -- replied after Touch {touch_num}."


def update_contact_replied(contact_id, current_score):
    """
    Bumps the actual numeric score (not just a label) and marks the
    contact as replied. This is the one place in the whole system that
    increases a score based on real engagement rather than static
    firmographic/role data.
    """
    old_score = int(current_score) if current_score else 0
    new_score = min(100, old_score + REPLY_SCORE_BONUS)  # cap at 100
    body = {"properties": {"outreach_tier": "Replied - In Sales Cycle", "lead_score": new_score}}
    r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{contact_id}", headers=HEADERS, json=body, timeout=30)
    if r.status_code >= 400:
        print(f"    HubSpot error {r.status_code}: {r.text[:300]}")
        return False, old_score, new_score
    return True, old_score, new_score


def log_note(contact_id, text):
    body = {
        "properties": {"hs_note_body": text, "hs_timestamp": int(time.time() * 1000)},
        "associations": [{"to": {"id": contact_id},
                           "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}],
    }
    r = requests.post(f"{BASE}/crm/v3/objects/notes", headers=HEADERS, json=body, timeout=30)
    return r.status_code < 400


print("Authenticating to Gmail...")
service = get_gmail_service()

print("Searching for our test reply-detection thread...")
threads = find_threads_by_subject(service, "TEST: Reply to this email")

if not threads:
    print("No matching thread found. Run add_test_contact.py first.")
else:
    for t in threads:
        thread_id = t["id"]
        has_reply = thread_has_reply(service, thread_id)
        print(f"\nThread {thread_id}: {'REPLY DETECTED' if has_reply else 'no reply yet'}")

        if has_reply:
            # We know this test always uses your own email as the contact
            from gmail_client import get_my_email
            my_email = get_my_email(service)
            contact = find_contact_by_email(my_email)
            if contact:
                already_replied = contact.get("properties", {}).get("outreach_tier") == "Replied - In Sales Cycle"
                if already_replied:
                    print(f"  Contact {contact['id']} already marked as replied -- skipping "
                          f"(no double-counting the bonus across multiple matching threads)")
                    continue
                current_score = contact.get("properties", {}).get("lead_score")
                attribution = describe_attribution(contact.get("properties", {}))
                ok, old_score, new_score = update_contact_replied(contact["id"], current_score)
                if ok:
                    log_note(contact["id"], f"Reply detected in Gmail thread. Contact has responded -- "
                                              f"moved to active sales cycle. Score boosted from {old_score} "
                                              f"to {new_score} (+{REPLY_SCORE_BONUS} reply bonus). "
                                              f"Attribution: {attribution} Needs human follow-up.")
                    print(f"  Updated HubSpot contact {contact['id']}: now 'Replied - In Sales Cycle', "
                          f"score {old_score} -> {new_score}")
                    print(f"  Attribution: {attribution}")
                else:
                    print(f"  FAILED to update HubSpot contact {contact['id']} -- check 'outreach_tier' "
                          f"property exists and Service Key has write scope")
            else:
                print("  Reply found, but no matching HubSpot contact for this email.")
