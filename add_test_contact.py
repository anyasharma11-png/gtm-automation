"""
add_test_contact.py

Creates ONE HubSpot contact using YOUR real email, so we can test reply
detection end to end (fake @example.com contacts can't receive real mail).

Then sends one real test email to that contact (yourself), so you can
reply to it like a real prospect would.

Run: python3 add_test_contact.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

from gmail_client import get_gmail_service, get_my_email, send_email

print("Authenticating to Gmail...")
service = get_gmail_service()
my_email = get_my_email(service)
print(f"Using your Gmail address: {my_email}\n")

print("Creating a HubSpot contact for reply-testing...")
body = {
    "properties": {
        "firstname": "Reply",
        "lastname": "Test",
        "email": my_email,
        "jobtitle": "VP Testing",
        "company": "Test Reply Co",
        "country": "United States",
        "source_event": "Reply Detection Test",
    }
}
r = requests.post(f"{BASE}/crm/v3/objects/contacts", headers=HEADERS, json=body, timeout=30)
if r.status_code == 409:
    print("  Contact already exists (that's fine, reusing it).")
elif r.status_code >= 400:
    print(f"  FAILED: {r.status_code} {r.text[:200]}")
    sys.exit(1)
else:
    contact_id = r.json()["id"]
    print(f"  Created contact {contact_id} with email {my_email}")

subject = "TEST: Reply to this email to test our reply detection"
message = (
    "This is a real test email sent by the lead pipeline.\n\n"
    "Reply to this email (anything at all) so we can test whether our "
    "reply-detection code correctly notices you responded.\n\n"
    "--- DEMO/TEST EMAIL ---"
)
print(f"\nSending a real test email to {my_email}...")
msg_id, thread_id = send_email(service, my_email, subject, message)
print(f"  Sent! Message ID: {msg_id}, Thread ID: {thread_id}")
print(f"\nGo check your Gmail inbox for a subject line starting with:")
print(f'  "{subject}"')
print("Reply to it (any text), then run: python3 check_replies.py")
