"""
harvest_to_drive.py

Pulls everything the Chrome extension needs (contacts, deals, coaching
briefs) from HubSpot and writes it as ONE structured JSON file to Google
Drive. This matches the GTM template's real architecture: harvest into
shared cloud storage, then let the UI read the pre-compiled file instead
of hitting the CRM live every time.

Requires the 'drive.file' scope added to your existing Google credentials
(same credentials.json used for Gmail) -- you'll need to delete token.json
and re-authenticate once after adding this scope.

Run: python3 harvest_to_drive.py
"""

import os
import json
import requests
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

load_dotenv()
HUBSPOT_KEY = os.getenv("HUBSPOT_SERVICE_KEY", "").strip()
HEADERS = {"Authorization": f"Bearer {HUBSPOT_KEY}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
DRIVE_FILENAME = "pipeline_data.json"


def get_google_creds():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds


def fetch_all_contacts():
    body = {
        "filterGroups": [{"filters": [{"propertyName": "source_event", "operator": "HAS_PROPERTY"}]}],
        "properties": ["firstname", "lastname", "company", "jobtitle", "lead_score",
                        "outreach_tier", "assigned_rep", "reengagement_touch_number",
                        "last_processed_date", "deal_context", "deal_persona"],
        "limit": 100,
    }
    r = requests.post(f"{BASE}/crm/v3/objects/contacts/search", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def fetch_all_deals():
    r = requests.get(f"{BASE}/crm/v3/objects/deals", headers=HEADERS,
                      params={"properties": "dealname,amount,dealstage,ai_coaching_brief", "limit": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


print("Pulling data from HubSpot...")
contacts = fetch_all_contacts()
deals = fetch_all_deals()
print(f"  {len(contacts)} contacts, {len(deals)} deals")

payload = {
    "generated_at": __import__("datetime").datetime.now().isoformat(),
    "contacts": contacts,
    "deals": deals,
}
json_bytes = json.dumps(payload, indent=2).encode("utf-8")

print("Authenticating to Google Drive...")
creds = get_google_creds()
drive_service = build("drive", "v3", credentials=creds)

print(f"Looking for existing '{DRIVE_FILENAME}' on Drive...")
results = drive_service.files().list(
    q=f"name='{DRIVE_FILENAME}' and trashed=false", fields="files(id, name)"
).execute()
existing = results.get("files", [])

media = MediaInMemoryUpload(json_bytes, mimetype="application/json")

if existing:
    file_id = existing[0]["id"]
    drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"Updated existing file (id: {file_id})")
else:
    file_metadata = {"name": DRIVE_FILENAME}
    created = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = created["id"]
    print(f"Created new file (id: {file_id})")

print(f"\nDone. File '{DRIVE_FILENAME}' is up to date on Google Drive.")
print(f"View it at: https://drive.google.com/file/d/{file_id}/view")
