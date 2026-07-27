"""
package_for_drive.py

Packages a clean, distributable copy of this whole project (code only --
NO personal secrets, NO your specific data) and uploads it as a zip file
to Google Drive, set to "anyone with the link can view/download". This
lets teammates without GitHub access grab a full working copy and set up
their own instance with their own HubSpot/Apollo/Slack/Google credentials.

Excludes: .env, credentials.json, token.json, apollo_usage.json,
apollo_seen_ids.json, venv/, __pycache__/, logs, queues/, .git/

Run: python3 package_for_drive.py
"""

import os
import shutil
import zipfile
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

EXCLUDE_NAMES = {
    ".env", "credentials.json", "token.json", "apollo_usage.json",
    "apollo_seen_ids.json", "venv", "__pycache__", ".git", "queues",
    "dashboard.html", "pipeline_log.txt", "digest_log.txt",
    "launchd_stdout.log", "launchd_stderr.log", "launchd_digest_stdout.log",
    "launchd_digest_stderr.log",
}
EXCLUDE_SUFFIXES = (".log", ".pyc")

ZIP_NAME = "gtm-automation-template.zip"


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


def should_include(path):
    parts = path.split(os.sep)
    if any(p in EXCLUDE_NAMES for p in parts):
        return False
    if path.endswith(EXCLUDE_SUFFIXES):
        return False
    return True


print("Building clean project zip (excluding secrets/personal data)...")
project_root = os.path.dirname(os.path.abspath(__file__))
zip_path = os.path.join("/tmp", ZIP_NAME)

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, project_root)
            if should_include(rel_path):
                zf.write(full_path, arcname=os.path.join("gtm-automation", rel_path))

print(f"Zip built: {zip_path}")

print("Authenticating to Google Drive...")
creds = get_google_creds()
drive_service = build("drive", "v3", credentials=creds)

print(f"Looking for existing '{ZIP_NAME}' on Drive...")
results = drive_service.files().list(
    q=f"name='{ZIP_NAME}' and trashed=false", fields="files(id, name)"
).execute()
existing = results.get("files", [])

media = MediaFileUpload(zip_path, mimetype="application/zip")

if existing:
    file_id = existing[0]["id"]
    drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"Updated existing file (id: {file_id})")
else:
    file_metadata = {"name": ZIP_NAME}
    created = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = created["id"]
    print(f"Created new file (id: {file_id})")

print("Setting sharing permission to 'anyone with the link can view'...")
drive_service.permissions().create(
    fileId=file_id, body={"type": "anyone", "role": "reader"}
).execute()

share_link = f"https://drive.google.com/file/d/{file_id}/view"
print(f"\nDone. Share this link with your team:")
print(f"  {share_link}")
print(f"\nThey can download the zip, unzip it, and follow the README to set up their own instance.")
