"""
gmail_client.py

Handles Gmail authentication, draft creation, real sending, and reply
detection for the standalone pipeline.

Setup (one-time):
  1. Put your downloaded OAuth file in this folder, renamed to: credentials.json
  2. pip3 install google-auth google-auth-oauthlib google-api-python-client
  3. First run will open your browser to approve access — after that, a
     token.json file is saved so you won't need to log in again.

IMPORTANT: this version adds the 'gmail.send' scope (previously only
'gmail.compose' for drafts). If you already have a token.json from before,
delete it so you're prompted to re-approve with the new scope:
    rm token.json

credentials.json and token.json are both git-ignored — never commit them.
"""

import os
import base64
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_gmail_service():
    """Authenticates once (browser popup), then reuses the saved token on future runs."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} not found. Download it from Google Cloud Console "
                    f"(APIs & Services -> Credentials) and place it in this folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_my_email(service) -> str:
    """Returns the Gmail address the pipeline is authenticated as."""
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


def create_draft(service, to_email: str, subject: str, body: str) -> str:
    """Creates a real Gmail draft. Returns the draft ID. Does NOT send anything."""
    message = MIMEText(body)
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return draft["id"]


def send_email(service, to_email: str, subject: str, body: str) -> str:
    """
    Actually SENDS an email (not a draft). Returns the sent message ID and thread ID.
    Use sparingly and deliberately -- this is a real send, unlike create_draft.
    """
    message = MIMEText(body)
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"], sent["threadId"]


def thread_has_reply(service, thread_id: str) -> bool:
    """Returns True if a thread has more than one message (i.e. someone replied)."""
    thread = service.users().threads().get(userId="me", id=thread_id).execute()
    return len(thread.get("messages", [])) > 1


def find_threads_by_subject(service, subject_contains: str, max_results: int = 20):
    """Searches Gmail for threads whose subject contains the given text."""
    query = f'subject:"{subject_contains}"'
    results = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
    return results.get("threads", [])
