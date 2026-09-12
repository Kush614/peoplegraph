"""Ingestion pipeline: mbox + ics -> Neo4j relationship graph.

    python -m peoplegraph.ingest            # full run against Aura
    python -m peoplegraph.ingest --dry-run  # parse + aggregate only, print stats, touch no DB
    python -m peoplegraph.ingest --reset    # wipe the graph first
    python -m peoplegraph.ingest --no-llm   # skip LLM extraction (heuristic fallback still runs)

Every write is a MERGE on the unique key, so re-running is safe.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import itertools
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config, db, llm
from .parse import Event, Message, parse_ics, parse_mbox

MAX_INTRO_PARTICIPANTS = 15
# Automated senders aren't relationships. Matched against the full address.
NOISE_ADDR = re.compile(
    r"(^|[.\-_+])(no-?reply|do-?not-?reply|notifications?|notify|mailer|newsletter|digest|alerts?|updates?|"
    r"support|billing|hello|team|info|marketing|bookface|coordination|events|community|careers|jobs|recruiting|calendar-notification|drive-shares|comments|invitations?)"
    r"(@|[.\-_+])|@(.*\.)?(luma-mail\.com|substack\.com|linear\.app|apartmentlist\.com|awsapps\.com|bing\.com|"
    r"privaterelay\.appleid\.com|calendar\.google\.com|docs\.google\.com|groups\.google\.com)$", re.IGNORECASE)
THREAD_SPLIT_DAYS = 45


def _thread(msgs: list[Message]) -> "Thread":
    tid = hashlib.sha1(msgs[0].id.encode()).hexdigest()[:16]
    return Thread(id=tid, subject=msgs[0].subject or "(no subject)", messages=msgs)


@dataclass
class Thread:
    id: str
    subject: str
    messages: list[Message] = field(default_factory=list)

    @property
    def participants(self) -> set[str]:
        s: set[str] = set()
        for m in self.messages:
            s.add(m.sender)
            s.update(m.recipients)
        return s

    @property
    def last_at(self) -> datetime:
        return max(m.date for m in self.messages)

    @property
    def first(self) -> Message:
        return min(self.messages, key=lambda m: m.date)


@dataclass
class GraphModel:
    my_email: str
    people: dict[str, dict]
    emailed: dict[tuple[str, str], dict]
    threads: list[Thread]
    participation: dict[tuple[str, str], int]
    companies: dict[str, str]
    works_at: set[tuple[str, str]]
    meetings: list[Event]
    attended: set[tuple[str, str]]
    intros: list[dict]


# ---------------------------------------------------------------- pure-python aggregation
def detect_owner(messages: list[Message]) -> str:
    if config.MY_EMAIL:
        return config.MY_EMAIL
    c: Counter[str] = Counter()
    for m in messages:
        c[m.sender] += 1
        c.update(m.recipients)
    if not c:
        raise SystemExit("no messages parsed")
    return c.most_common(1)[0][0]


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_threads(messages: list[Message], my_email: str = "") -> list[Thread]:
    """Group by reply chain (In-Reply-To), plus normalized subject *within the same counterparties*
    (so two unrelated "Quick question" threads with different people stay separate)."""
    uf = _UnionFind()
    by_id = {m.id for m in messages}
    for m in messages:
        uf.find(m.id)
        if m.in_reply_to and m.in_reply_to in by_id:
            uf.union(m.in_reply_to, m.id)
        others = sorted({m.sender, *m.recipients} - {my_email})
        uf.union(f"subject:{m.norm_subject}|{','.join(others)}", m.id)
    groups: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        groups[uf.find(m.id)].append(m)
    threads = []
    for msgs in groups.values():
        msgs.sort(key=lambda m: m.date)
        # A subject that comes back after a long silence is a new conversation, unless it's a real reply.
        chunk: list[Message] = []
        for m in msgs:
            if chunk and (m.date - chunk[-1].date).days > THREAD_SPLIT_DAYS and m.in_reply_to not in {x.id for x in chunk}:
                threads.append(_thread(chunk))
                chunk = []
            chunk.append(m)
        threads.append(_thread(chunk))
    threads.sort(key=lambda t: t.first.date)
    return threads


def is_noise(addr: str, my_email: str) -> bool:
    return addr != my_email and bool(NOISE_ADDR.search(addr))


def build_model(messages: list[Message], events: list[Event], my_email: str) -> GraphModel:
    # Drop automated senders and strip them from recipient lists.
    kept: list[Message] = []
    for m in messages:
        if is_noise(m.sender, my_email):
            continue
        m.recipients = [r for r in m.recipients if not is_noise(r, my_email)]
        if my_email not in (m.sender, *m.recipients) and len(m.recipients) > 20:
            continue  # mass mailings I'm bcc'd on
        kept.append(m)
    messages = kept
    for ev in events:
        ev.attendees = [a for a in ev.attendees if not is_noise(a, my_email)]
    people: dict[str, dict] = {}
    emailed: dict[tuple[str, str], dict] = {}

    def touch(addr: str, name: str | None, d: date) -> None:
        p = people.setdefault(addr, {"email": addr, "name": None, "firstSeen": d, "lastSeen": d, "isMe": addr == my_email})
        if name and "@" not in name and (not p["name"] or len(name) > len(p["name"])):
            p["name"] = name
        p["firstSeen"] = min(p["firstSeen"], d)
        p["lastSeen"] = max(p["lastSeen"], d)

    for m in messages:
        d = m.date.date()
        touch(m.sender, m.sender_name, d)
        for r in m.recipients:
            touch(r, m.names.get(r), d)
            e = emailed.setdefault((m.sender, r), {"count": 0, "firstDate": d, "lastDate": d})
            e["count"] += 1
            e["firstDate"] = min(e["firstDate"], d)
            e["lastDate"] = max(e["lastDate"], d)

    threads = build_threads(messages, my_email)
    participation: dict[tuple[str, str], int] = defaultdict(int)
    for t in threads:
        for m in t.messages:
            for addr in [m.sender, *m.recipients]:
                participation[(addr, t.id)] += 1

    companies: dict[str, str] = {}
    works_at: set[tuple[str, str]] = set()
    for addr in people:
        domain = addr.rsplit("@", 1)[-1]
        if domain in config.FREEMAIL_DOMAINS or "." not in domain:
            continue
        companies.setdefault(domain, domain.split(".")[0].replace("-", " ").title())
        works_at.add((addr, domain))

    attended: set[tuple[str, str]] = set()
    for ev in events:
        for a in ev.attendees:
            people.setdefault(a, {"email": a, "name": None, "firstSeen": None, "lastSeen": None, "isMe": a == my_email})
            attended.add((a, ev.id))

    intros = detect_introductions(threads)
    for p in people.values():
        if not p["name"]:
            p["name"] = p["email"].split("@")[0].replace(".", " ").title()
    return GraphModel(my_email, people, emailed, threads, dict(participation), companies, works_at, events, attended, intros)


def detect_introductions(threads: list[Thread]) -> list[dict]:
    """A introduces B and C when A starts a thread including both and B, C never co-occurred before."""
    seen: set[frozenset[str]] = set()
    intros: list[dict] = []
    for t in threads:  # already chronological
        parts = t.participants
        if len(parts) >= 3 and len(parts) <= MAX_INTRO_PARTICIPANTS:
            starter = t.first.sender
            others = sorted(parts - {starter})
            introduced: set[str] = set()
            for b, c in itertools.combinations(others, 2):
                if frozenset((b, c)) not in seen:
                    introduced.update((b, c))
            for x in introduced:
                intros.append({"from": starter, "to": x, "viaThreadId": t.id, "date": t.first.date.date()})
        for pair in itertools.combinations(sorted(parts), 2):
            seen.add(frozenset(pair))
    return intros


# ---------------------------------------------------------------- neo4j writes
def write_model(driver, gm: GraphModel) -> None:
    db.run(driver, """
        UNWIND $rows AS r
        MERGE (p:Person {email: r.email})
        SET p.name = r.name, p.isMe = r.isMe,
            p.firstSeen = coalesce(r.firstSeen, p.firstSeen),
            p.lastSeen = coalesce(r.lastSeen, p.lastSeen)
    """, rows=list(gm.people.values()))
    db.run(driver, """
        UNWIND $rows AS r
        MATCH (a:Person {email: r.from}), (b:Person {email: r.to})
        MERGE (a)-[e:EMAILED]->(b)
        SET e.count = r.count, e.firstDate = r.firstDate, e.lastDate = r.lastDate
    """, rows=[{"from": a, "to": b, **v} for (a, b), v in gm.emailed.items()])
    db.run(driver, """
        UNWIND $rows AS r
        MERGE (c:Company {domain: r.domain}) SET c.name = r.name
    """, rows=[{"domain": d, "name": n} for d, n in gm.companies.items()])
    db.run(driver, """
        UNWIND $rows AS r
        MATCH (p:Person {email: r.email}), (c:Company {domain: r.domain})
        MERGE (p)-[w:WORKS_AT]->(c) SET w.inferredFrom = 'domain'
    """, rows=[{"email": e, "domain": d} for e, d in gm.works_at])
    db.run(driver, """
        UNWIND $rows AS r
        MERGE (t:Thread {id: r.id})
        SET t.subject = r.subject, t.lastMessageAt = r.lastMessageAt, t.messageCount = r.messageCount
    """, rows=[{"id": t.id, "subject": t.subject, "lastMessageAt": t.last_at, "messageCount": len(t.messages)} for t in gm.threads])
    db.run(driver, """
        UNWIND $rows AS r
        MATCH (p:Person {email: r.email}), (t:Thread {id: r.thread})
        MERGE (p)-[x:PARTICIPATED_IN]->(t) SET x.messageCount = r.count
    """, rows=[{"email": e, "thread": t, "count": c} for (e, t), c in gm.participation.items()])
    db.run(driver, """
        UNWIND $rows AS r
        MERGE (m:Meeting {id: r.id}) SET m.title = r.title, m.start = r.start, m.end = r.end
    """, rows=[{"id": e.id, "title": e.title, "start": e.start, "end": e.end} for e in gm.meetings])
    db.run(driver, """
        UNWIND $rows AS r
        MATCH (p:Person {email: r.email}), (m:Meeting {id: r.meeting})
        MERGE (p)-[:ATTENDED]->(m)
    """, rows=[{"email": e, "meeting": m} for e, m in gm.attended])
    db.run(driver, """
        UNWIND $rows AS r
        MATCH (a:Person {email: r.from}), (b:Person {email: r.to})
        MERGE (a)-[i:INTRODUCED {viaThreadId: r.viaThreadId}]->(b) SET i.date = r.date
    """, rows=gm.intros)


def run_extraction(driver, gm: GraphModel, use_llm: bool) -> dict:
    """LLM (or heuristic) extraction on the most recent threads. Never raises."""
    recent = sorted(gm.threads, key=lambda t: t.last_at, reverse=True)[: config.EXTRACT_THREAD_LIMIT]
    items = []
    for t in recent:
        snippet = "\n".join(f"[{m.sender}] {m.body[:400]}".replace("\n", " ") for m in t.messages[-4:])
        items.append({"id": t.id, "subject": t.subject, "participants": sorted(t.participants), "snippet": snippet})
    stats = {"threads": 0, "topics": 0, "commitments": 0, "errors": 0}
    if not use_llm:
        config.LLM_API_KEY = ""  # forces heuristic path
    for i in range(0, len(items), config.EXTRACT_BATCH_SIZE):
        batch = items[i:i + config.EXTRACT_BATCH_SIZE]
        try:
            results = llm.extract_batch(batch, gm.my_email)
        except Exception as exc:  # extraction must never break ingest
            stats["errors"] += 1
            print(f"  ! extraction batch {i // config.EXTRACT_BATCH_SIZE} failed: {exc}", file=sys.stderr)
            try:
                results = [llm._heuristic(it, gm.my_email) for it in batch]
            except Exception:
                continue
        topic_rows, commit_rows = [], []
        for r in results:
            stats["threads"] += 1
            for topic in r.topics:
                name = topic.strip().lower()[:60]
                if name:
                    topic_rows.append({"thread": r.id, "topic": name})
            for c in r.commitments:
                other = c.counterparty.lower().strip()
                thread = next((t for t in recent if t.id == r.id), None)
                if thread and (other == gm.my_email or other not in thread.participants):
                    others = sorted((p for p in thread.participants if p != gm.my_email),
                                    key=lambda p: -gm.participation.get((p, thread.id), 0))
                    other = others[0] if others else ""
                if not other:
                    continue
                owes, owed_to = (gm.my_email, other) if c.direction == "owed_by_me" else (other, gm.my_email)
                commit_rows.append({
                    "id": hashlib.sha1(f"{r.id}|{c.text}".encode()).hexdigest()[:16],
                    "text": c.text.strip()[:300], "direction": c.direction, "dueHint": c.dueHint or "",
                    "thread": r.id, "owes": owes, "owedTo": owed_to,
                    "createdAt": (thread.last_at if thread else datetime.now()),
                })
        db.run(driver, """
            UNWIND $rows AS r
            MATCH (t:Thread {id: r.thread})
            MERGE (tp:Topic {name: r.topic})
            MERGE (t)-[:ABOUT]->(tp)
        """, rows=topic_rows)
        db.run(driver, """
            UNWIND $rows AS r
            MATCH (t:Thread {id: r.thread}), (a:Person {email: r.owes}), (b:Person {email: r.owedTo})
            MERGE (c:Commitment {id: r.id})
            ON CREATE SET c.status = 'open', c.createdAt = r.createdAt
            SET c.text = r.text, c.direction = r.direction, c.dueHint = r.dueHint, c.sourceThreadId = r.thread
            MERGE (c)-[:MADE_IN]->(t)
            MERGE (a)-[:OWES]->(c)
            MERGE (c)-[:OWED_TO]->(b)
        """, rows=commit_rows)
        stats["topics"] += len(topic_rows)
        stats["commitments"] += len(commit_rows)
    return stats


# ---------------------------------------------------------------- entrypoint
def summarize(gm: GraphModel) -> None:
    strong = sorted(((v["count"], a, b) for (a, b), v in gm.emailed.items() if gm.my_email in (a, b)), reverse=True)[:8]
    print(f"owner        : {gm.my_email}")
    print(f"people       : {len(gm.people)}")
    print(f"companies    : {len(gm.companies)}  {sorted(gm.companies)}")
    print(f"EMAILED edges: {len(gm.emailed)}")
    print(f"threads      : {len(gm.threads)}  (>=3 participants: {sum(1 for t in gm.threads if len(t.participants) >= 3)})")
    print(f"meetings     : {len(gm.meetings)}  attended edges: {len(gm.attended)}")
    print(f"introductions: {len(gm.intros)}")
    for i in gm.intros:
        print(f"   {i['from']} -> {i['to']}  ({i['date']})")
    print("top edges    :")
    for c, a, b in strong:
        print(f"   {a} -> {b}: {c}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mbox", default=str(config.MBOX_PATH))
    ap.add_argument("--ics", default=str(config.ICS_PATH))
    ap.add_argument("--dry-run", action="store_true", help="parse and aggregate only; no DB writes")
    ap.add_argument("--reset", action="store_true", help="DETACH DELETE everything before ingest")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM; use heuristic extraction")
    args = ap.parse_args(argv)

    t0 = time.time()
    from pathlib import Path
    messages = parse_mbox(Path(args.mbox))
    events = parse_ics(Path(args.ics))
    my_email = detect_owner(messages)
    gm = build_model(messages, events, my_email)
    print(f"parsed {len(messages)} messages, {len(events)} events in {time.time() - t0:.1f}s")
    summarize(gm)
    if args.dry_run:
        return

    driver = db.get_driver()
    try:
        driver.verify_connectivity()
        if args.reset:
            print("resetting graph…")
            db.reset(driver)
        db.ensure_schema(driver)
        print("writing graph…")
        write_model(driver, gm)
        print("extracting topics + commitments…")
        stats = run_extraction(driver, gm, use_llm=not args.no_llm and bool(config.LLM_API_KEY))
        print(f"   {stats}")
        scored = db.compute_warmth(driver)
        print(f"warmth computed for {scored} people")
        print(f"done in {time.time() - t0:.1f}s")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
