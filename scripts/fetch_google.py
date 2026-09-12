"""Pull real Gmail + Google Calendar data via the Google APIs into data/inbox.mbox + data/calendar.ics.

Auth, option A — all CLI, no console clicking (uses gcloud's own OAuth client):
  gcloud auth login
  gcloud projects create peoplegraph-<something> && gcloud config set project peoplegraph-<something>
  gcloud services enable gmail.googleapis.com calendar-json.googleapis.com
  gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar.readonly
  gcloud auth application-default set-quota-project peoplegraph-<something>
Auth, option B — a Desktop OAuth client JSON from the Cloud console saved as data/credentials.json.

Then:
  .venv/bin/python scripts/fetch_google.py
  .venv/bin/python -m peoplegraph.ingest --reset

Read-only scopes only. Nothing is written to your Google account.
"""
from __future__ import annotations

import argparse
import base64
import mailbox
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from icalendar import Calendar, Event, vCalAddress, vText

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
# Skip the noise that says nothing about relationships.
DEFAULT_QUERY = "-category:promotions -category:social -category:updates -category:forums -in:spam -in:trash"


def auth() -> Credentials:
    token, secrets = DATA / "token.json", DATA / "credentials.json"
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif secrets.exists():
        creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
    else:
        # gcloud auth application-default login --scopes=...  (see docstring)
        try:
            creds, _ = google.auth.default(scopes=SCOPES)
        except google.auth.exceptions.DefaultCredentialsError:
            sys.exit("no credentials: run the gcloud steps at the top of this file, or save data/credentials.json")
        if not creds.valid:
            creds.refresh(Request())
        return creds  # ADC manages its own refresh token; nothing to cache
    token.write_text(creds.to_json())
    return creds


def fetch_gmail(creds, days: int, limit: int, query: str) -> tuple[str, int]:
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    q = f"{query} newer_than:{days}d"
    ids: list[str] = []
    token = None
    while len(ids) < limit:
        resp = svc.users().messages().list(userId="me", q=q, maxResults=min(500, limit - len(ids)), pageToken=token).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    print(f"gmail: {len(ids)} message ids for {me} (query: {q!r})")

    out = DATA / "inbox.mbox"
    if out.exists():
        out.unlink()
    box = mailbox.mbox(str(out))
    written = 0

    failed: list[str] = []

    def on_msg(rid, resp, exc):
        nonlocal written
        if exc is not None:
            failed.append(rid)  # mostly 429 "Too many concurrent requests" — retried below
            return
        raw = base64.urlsafe_b64decode(resp["raw"])
        box.add(mailbox.mboxMessage(raw))
        written += 1

    def run_batches(todo: list[str], size: int):
        for i in range(0, len(todo), size):
            batch = svc.new_batch_http_request(callback=on_msg)
            for mid in todo[i:i + size]:
                batch.add(svc.users().messages().get(userId="me", id=mid, format="raw"), request_id=mid)
            batch.execute()
            print(f"  fetched {written}/{len(ids)}", end="\r", flush=True)
            time.sleep(0.6)

    # Gmail caps *concurrent* requests per user, so keep batches small and retry stragglers with backoff.
    run_batches(ids, 10)
    for attempt in range(1, 6):
        if not failed:
            break
        retry, failed = failed, []
        print(f"\n  retrying {len(retry)} throttled messages (attempt {attempt})")
        time.sleep(2 * attempt)
        run_batches(retry, 5)
    if failed:
        print(f"\n  gave up on {len(failed)} messages", file=sys.stderr)
    box.flush()
    box.close()
    print(f"\ngmail: wrote {written} messages -> {out}")
    return me, written


def fetch_calendar(creds, days_back: int, days_forward: int) -> int:
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    cal = Calendar()
    cal.add("prodid", "-//PeopleGraph fetch_google//EN")
    cal.add("version", "2.0")
    n = 0
    token = None
    while True:
        resp = svc.events().list(
            calendarId="primary", singleEvents=True, orderBy="startTime", maxResults=2500,
            timeMin=(now - timedelta(days=days_back)).isoformat(), timeMax=(now + timedelta(days=days_forward)).isoformat(),
            pageToken=token,
        ).execute()
        for ev in resp.get("items", []):
            if ev.get("status") == "cancelled" or "dateTime" not in ev.get("start", {}):
                continue  # skip all-day events and cancellations
            attendees = [a["email"].lower() for a in ev.get("attendees", []) if a.get("email") and not a.get("resource")]
            if len(attendees) < 2:
                continue  # solo blocks aren't relationships
            e = Event()
            e.add("uid", ev["id"])
            e.add("summary", ev.get("summary") or "(untitled)")
            e.add("dtstart", datetime.fromisoformat(ev["start"]["dateTime"]))
            e.add("dtend", datetime.fromisoformat(ev["end"]["dateTime"]))
            org = ev.get("organizer", {}).get("email")
            if org:
                e["organizer"] = vCalAddress(f"mailto:{org.lower()}")
            for a in attendees:
                att = vCalAddress(f"mailto:{a}")
                att.params["partstat"] = vText("ACCEPTED")
                e.add("attendee", att, encode=0)
            cal.add_component(e)
            n += 1
        token = resp.get("nextPageToken")
        if not token:
            break
    out = DATA / "calendar.ics"
    out.write_bytes(cal.to_ical())
    print(f"calendar: wrote {n} meetings with attendees -> {out}")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=540, help="how far back to pull mail + calendar (default 18 months)")
    ap.add_argument("--limit", type=int, default=2000, help="max emails to fetch")
    ap.add_argument("--query", default=DEFAULT_QUERY, help="extra Gmail search filter")
    ap.add_argument("--skip-mail", action="store_true")
    ap.add_argument("--skip-calendar", action="store_true")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    creds = auth()
    if not args.skip_mail:
        me, _ = fetch_gmail(creds, args.days, args.limit, args.query)
        (DATA / "owner.txt").write_text(me + "\n")
        print(f"owner recorded in data/owner.txt: {me}")
    if not args.skip_calendar:
        fetch_calendar(creds, args.days, 30)
    print("\nnext: .venv/bin/python -m peoplegraph.ingest --reset")


if __name__ == "__main__":
    main()
