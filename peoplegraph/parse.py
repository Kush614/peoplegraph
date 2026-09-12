"""Parse a Gmail Takeout mbox and an .ics calendar into plain dataclasses."""
from __future__ import annotations

import email.utils
from email.header import decode_header, make_header
import hashlib
import mailbox
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from icalendar import Calendar

BODY_CHARS = 1500
_SUBJECT_PREFIX = re.compile(r"^\s*((re|fwd?|fw|aw|wg)\s*:\s*)+", re.IGNORECASE)


@dataclass
class Message:
    id: str
    subject: str
    norm_subject: str
    date: datetime
    sender: str
    sender_name: str
    recipients: list[str]          # to + cc, lowercased, deduped, sender excluded
    names: dict[str, str]          # email -> display name seen in this message
    in_reply_to: str | None
    body: str


@dataclass
class Event:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)


def normalize_subject(subject: str) -> str:
    s = _SUBJECT_PREFIX.sub("", subject or "").strip()
    return re.sub(r"\s+", " ", s).lower() or "(no subject)"


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _addresses(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    out = []
    for name, addr in email.utils.getaddresses([str(raw)]):
        addr = addr.strip().lower()
        if "@" not in addr:
            continue
        if "=?" in name:  # RFC 2047 encoded display name
            try:
                name = str(make_header(decode_header(name)))
            except Exception:
                pass
        out.append((addr, name.strip().strip('"').replace("_", " ")))
    return out


def _text_body(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        return text.strip()[:BODY_CHARS]
    return ""


def parse_mbox(path: Path) -> list[Message]:
    box = mailbox.mbox(str(path))
    messages: list[Message] = []
    for i, msg in enumerate(box):
        senders = _addresses(msg.get("From"))
        if not senders:
            continue
        sender, sender_name = senders[0]
        raw_date = msg.get("Date")
        try:
            dt = _aware(email.utils.parsedate_to_datetime(raw_date)) if raw_date else None
        except (TypeError, ValueError):
            dt = None
        if dt is None:
            continue
        names: dict[str, str] = {sender: sender_name}
        recipients: list[str] = []
        for addr, name in _addresses(msg.get("To")) + _addresses(msg.get("Cc")):
            if addr == sender or addr in recipients:
                continue
            recipients.append(addr)
            if name:
                names[addr] = name
        mid = (msg.get("Message-ID") or "").strip() or f"<synthetic-{i}-{hashlib.sha1(raw_date.encode()).hexdigest()[:8]}>"
        irt = (msg.get("In-Reply-To") or "").strip() or None
        subject = str(msg.get("Subject") or "").replace("\n", " ").strip()
        messages.append(Message(
            id=mid, subject=subject, norm_subject=normalize_subject(subject), date=dt,
            sender=sender, sender_name=sender_name, recipients=recipients, names=names,
            in_reply_to=irt, body=_text_body(msg),
        ))
    messages.sort(key=lambda m: m.date)
    return messages


def parse_ics(path: Path) -> list[Event]:
    if not path.exists():
        return []
    cal = Calendar.from_ical(path.read_bytes())
    events: list[Event] = []
    for comp in cal.walk("VEVENT"):
        start = comp.get("DTSTART").dt
        end_prop = comp.get("DTEND")
        end = end_prop.dt if end_prop else start
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time())
        if isinstance(end, date) and not isinstance(end, datetime):
            end = datetime.combine(end, datetime.min.time())
        attendees = []
        raw = comp.get("ATTENDEE", [])
        if not isinstance(raw, list):
            raw = [raw]
        organizer = comp.get("ORGANIZER")
        if organizer:
            raw = raw + [organizer]
        for a in raw:
            addr = str(a).replace("mailto:", "").replace("MAILTO:", "").strip().lower()
            if "@" in addr and addr not in attendees:
                attendees.append(addr)
        events.append(Event(
            id=str(comp.get("UID") or hashlib.sha1(str(comp.get("SUMMARY")).encode()).hexdigest()[:16]),
            title=str(comp.get("SUMMARY") or "(untitled)"),
            start=_aware(start), end=_aware(end), attendees=attendees,
        ))
    events.sort(key=lambda e: e.start)
    return events
