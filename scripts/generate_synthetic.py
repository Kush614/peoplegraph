"""Generate a deterministic synthetic Gmail mbox + calendar .ics for PeopleGraph demos.

Storyline (seeded, dates are relative to *today* so the demo always looks fresh):
  * ~40 people across 8 companies (Stripe and Notion are the "recognizable" targets)
  * 5 strong, reciprocal, recent ties
  * 4 ties that were hot ~6 months ago and have been silent since (feeds /cold)
  * 3 planted introductions (3-party threads)
  * 5 open commitments in recent threads
  * 20 meetings, 3 of them upcoming (feeds the pre-meeting brief)
"""
from __future__ import annotations

import argparse
import email.utils
import mailbox
import random
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from icalendar import Calendar, Event, vCalAddress, vText

SEED = 42
ME = ("Kush", "kush@lumenlabs.io")

COMPANIES = {
    "lumenlabs.io": "Lumen Labs",
    "stripe.com": "Stripe",
    "notion.so": "Notion",
    "acme-robotics.com": "Acme Robotics",
    "northwind.io": "Northwind",
    "bluefin.capital": "Bluefin Capital",
    "orbital.dev": "Orbital",
    "helio.health": "Helio Health",
}

# (name, email, tier)  tiers: strong | cold | weak | reach (never emailed directly by me)
PEOPLE = [
    # Lumen Labs (my company)
    ("Sofia Alvarez", "sofia@lumenlabs.io", "strong"),
    ("Ben Carter", "ben@lumenlabs.io", "weak"),
    ("Yuki Tanaka", "yuki@lumenlabs.io", "weak"),
    ("Ines Moreau", "ines@lumenlabs.io", "weak"),
    # Stripe — target company; reachable only through others
    ("Priya Nair", "priya.nair@stripe.com", "reach"),
    ("Omar Haddad", "omar.haddad@stripe.com", "reach"),
    ("Ravi Iyer", "ravi.iyer@stripe.com", "reach"),
    ("Claire Dubois", "claire.dubois@stripe.com", "reach"),
    # Notion — target company; one weak direct tie + one intro
    ("Sam Park", "sam.park@notion.so", "weak"),
    ("Lena Fischer", "lena.fischer@notion.so", "reach"),
    ("Diego Santos", "diego.santos@notion.so", "reach"),
    # Acme Robotics
    ("Aisha Bello", "aisha@acme-robotics.com", "strong"),
    ("Marcus Lee", "marcus@acme-robotics.com", "cold"),
    ("Rahul Mehta", "rahul@acme-robotics.com", "reach"),
    ("Grace Liu", "grace@acme-robotics.com", "weak"),
    ("Tomasz Nowak", "tomasz@acme-robotics.com", "weak"),
    # Northwind
    ("Maya Chen", "maya@northwind.io", "strong"),
    ("Hannah Kim", "hannah@northwind.io", "cold"),
    ("Luis Ortega", "luis@northwind.io", "weak"),
    ("Nadia Petrova", "nadia@northwind.io", "weak"),
    ("Owen Brooks", "owen@northwind.io", "weak"),
    # Bluefin Capital
    ("Jonas Weber", "jonas@bluefin.capital", "strong"),
    ("Tom Okafor", "tom@bluefin.capital", "cold"),
    ("Amara Singh", "amara@bluefin.capital", "weak"),
    ("Felix Brandt", "felix@bluefin.capital", "weak"),
    # Orbital
    ("Dev Raman", "dev@orbital.dev", "strong"),
    ("Chloe Martin", "chloe@orbital.dev", "weak"),
    ("Kenji Sato", "kenji@orbital.dev", "weak"),
    ("Zara Ahmed", "zara@orbital.dev", "weak"),
    ("Pete Hallam", "pete@orbital.dev", "weak"),
    # Helio Health
    ("Elena Rossi", "elena@helio.health", "cold"),
    ("Noah Green", "noah@helio.health", "weak"),
    ("Ivy Zhang", "ivy@helio.health", "weak"),
    ("Karl Jensen", "karl@helio.health", "weak"),
    # Freemail
    ("Arjun Kapoor", "arjun.kapoor@gmail.com", "weak"),
    ("Mia Rodriguez", "mia.rodriguez@gmail.com", "weak"),
    ("Leo Nguyen", "leo.nguyen@gmail.com", "weak"),
    ("Sara Lindqvist", "sara.lindqvist@gmail.com", "weak"),
]
NAME = {e: n for n, e, _ in PEOPLE}
NAME[ME[1]] = ME[0]

TOPICS = {
    "strong": [
        ("Q4 roadmap sync", "the Q4 roadmap"), ("Pricing deck v3", "the pricing deck"),
        ("Pilot kickoff", "the pilot"), ("Board update draft", "the board update"),
        ("Hiring: senior backend", "the backend hire"), ("Partnership terms", "partnership terms"),
        ("Data pipeline latency", "pipeline latency"), ("Offsite planning", "the offsite"),
    ],
    "cold": [
        ("Integration proposal", "the integration proposal"), ("Vendor evaluation", "the vendor eval"),
        ("Contract renewal", "the renewal"), ("Security review", "the security review"),
        ("Conference follow-up", "the conference"),
    ],
    "weak": [
        ("Quick question", "your question"), ("Coffee next week?", "coffee"),
        ("Intro request", "the intro"), ("Podcast episode", "the episode"),
        ("Invoice #4471", "the invoice"), ("Slides from Tuesday", "the slides"),
        ("Feedback on the draft", "the draft"),
    ],
}
OPENERS = ["Hi {name},", "Hey {name},", "{name} —", "Hello {name},"]
LINES = [
    "Following up on {topic}. I think we're close but I want to make sure we're aligned on scope.",
    "Thanks for the quick turnaround on {topic}. A couple of notes inline below.",
    "Circling back on {topic} — did you get a chance to look at the numbers?",
    "Quick update on {topic}: the team reviewed it and the feedback was positive overall.",
    "Re {topic}: let's lock the timeline this week so we can brief the wider group.",
    "I looked over {topic} last night. Two things stood out that we should discuss.",
    "Sounds good on {topic}. Let me know what works for a 30 min call.",
    "Appreciate the context on {topic}. That resolves my main concern.",
]
CLOSERS = ["Best,\n{me}", "Thanks,\n{me}", "Cheers,\n{me}", "Talk soon,\n{me}"]


def _dt(base: datetime, days_ago: float, rng: random.Random) -> datetime:
    return base - timedelta(days=days_ago, hours=rng.randint(8, 18), minutes=rng.randint(0, 59))


class Builder:
    def __init__(self, base: datetime):
        self.rng = random.Random(SEED)
        self.base = base
        self.msgs: list[dict] = []
        self.n = 0

    def msg(self, sender, to, date, subject, body, cc=(), reply_to=None) -> str:
        self.n += 1
        mid = f"<msg-{self.n:04d}@synthetic.peoplegraph>"
        self.msgs.append(dict(id=mid, sender=sender, to=list(to), cc=list(cc), date=date,
                              subject=subject, body=body, reply_to=reply_to))
        return mid

    def body(self, to_email, topic_phrase, sender_email, extra=None):
        r = self.rng
        first = NAME[to_email].split()[0]
        lines = [r.choice(OPENERS).format(name=first), "", r.choice(LINES).format(topic=topic_phrase)]
        if extra:
            lines += ["", extra]
        lines += ["", r.choice(CLOSERS).format(me=NAME[sender_email].split()[0])]
        return "\n".join(lines)

    def thread(self, a, b, subject, phrase, days_ago, n_msgs, extras=None, cc=()):
        """A two-party thread of n_msgs alternating between a and b, starting days_ago."""
        extras = extras or {}
        prev = None
        sender, recip = a, b
        d = days_ago
        for i in range(n_msgs):
            date = _dt(self.base, d, self.rng)
            subj = subject if i == 0 else f"Re: {subject}"
            prev = self.msg(sender, [recip], date, subj,
                            self.body(recip, phrase, sender, extras.get(i)), cc=cc, reply_to=prev)
            sender, recip = recip, sender
            d -= self.rng.uniform(0.2, 2.5)
            if d < 0:
                d = 0.05

    def relationship(self, other, tier, n_threads, window):
        """Many threads between me and `other`, with days_ago sampled from `window`."""
        lo, hi = window
        for _ in range(n_threads):
            subject, phrase = self.rng.choice(TOPICS[tier])
            start = self.rng.uniform(lo, hi)
            n_msgs = self.rng.choice([1, 2, 2, 3, 3, 4, 5]) if tier != "weak" else self.rng.choice([1, 1, 2])
            a, b = (ME[1], other) if self.rng.random() < 0.55 else (other, ME[1])
            self.thread(a, b, subject, phrase, start, n_msgs)


def build(base: datetime):
    b = Builder(base)
    r = b.rng
    me = ME[1]
    tiers = {e: t for _, e, t in PEOPLE}
    strong = [e for e, t in tiers.items() if t == "strong"]
    cold = [e for e, t in tiers.items() if t == "cold"]
    weak = [e for e, t in tiers.items() if t == "weak"]

    # Strong ties: heavy, reciprocal, recent. Half the threads in the last 60 days.
    for e in strong:
        b.relationship(e, "strong", 6, (60, 540))
        b.relationship(e, "strong", 6, (1, 60))
    # Cold ties: hot between 5 and 9 months ago, nothing since.
    for e in cold:
        b.relationship(e, "cold", 5, (150, 270))
    # Weak ties: a couple of short exchanges, scattered.
    for e in weak:
        b.relationship(e, "weak", r.choice([1, 2, 2, 3]), (5, 540))
    # Sam Park at Notion: weak direct tie, 8 months ago.
    b.thread(me, "sam.park@notion.so", "Notion API access", "API access", 240, 2)

    # --- Planted introductions (3-party threads; introducer emails both) ---
    # 1. Maya (Northwind) introduces me to Rahul (Acme) — 4 months ago; we follow up.
    intro = b.msg("maya@northwind.io", [me, "rahul@acme-robotics.com"], _dt(base, 120, r),
                  "Intro: Kush <> Rahul", "Kush, meet Rahul — he runs platform at Acme and is looking at exactly "
                  "the problem you described. Rahul, Kush is building Lumen. I'll let you two take it from here.\n\nMaya")
    b.msg("rahul@acme-robotics.com", [me], _dt(base, 118, r), "Re: Intro: Kush <> Rahul",
          "Thanks Maya! Kush, great to meet you. Free Thursday for a quick call?\n\nRahul", cc=["maya@northwind.io"], reply_to=intro)
    b.msg(me, ["rahul@acme-robotics.com"], _dt(base, 117, r), "Re: Intro: Kush <> Rahul",
          "Thursday works. I'll send an invite.\n\nKush", reply_to=intro)
    # 2. Dev (Orbital) introduces me to Priya (Stripe) — 12 days ago. Priya makes a commitment.
    intro = b.msg("dev@orbital.dev", [me, "priya.nair@stripe.com"], _dt(base, 12, r),
                  "Intro: Kush (Lumen) <> Priya (Stripe partnerships)",
                  "Priya — Kush is building Lumen; I mentioned your partner program. Kush — Priya leads "
                  "platform partnerships at Stripe. Over to you both.\n\nDev")
    b.msg("priya.nair@stripe.com", [me], _dt(base, 11, r), "Re: Intro: Kush (Lumen) <> Priya (Stripe partnerships)",
          "Thanks Dev. Kush, happy to chat. I'll share the partner onboarding checklist so you can see what's "
          "involved before we meet.\n\nPriya", cc=["dev@orbital.dev"], reply_to=intro)
    b.msg(me, ["priya.nair@stripe.com"], _dt(base, 10, r), "Re: Intro: Kush (Lumen) <> Priya (Stripe partnerships)",
          "That would be great, thanks Priya. Looking forward to it.\n\nKush", cc=["dev@orbital.dev"], reply_to=intro)
    # 3. Jonas (Bluefin) introduces me to Lena (Notion) — 2 months ago.
    intro = b.msg("jonas@bluefin.capital", [me, "lena.fischer@notion.so"], _dt(base, 60, r),
                  "Intro: Kush <> Lena", "Lena, Kush is a founder I back at Lumen. Kush, Lena runs "
                  "integrations partnerships at Notion.\n\nJonas")
    b.msg("lena.fischer@notion.so", [me], _dt(base, 58, r), "Re: Intro: Kush <> Lena",
          "Nice to meet you Kush — let's find 20 minutes.\n\nLena", cc=["jonas@bluefin.capital"], reply_to=intro)

    # --- CC-only threads: my strong ties talking to Stripe/Notion people with me on copy ---
    b.msg("dev@orbital.dev", ["ravi.iyer@stripe.com", "omar.haddad@stripe.com"], _dt(base, 40, r),
          "Stripe x Orbital: billing integration", "Ravi, Omar — attaching the integration notes. Kush (cc) has "
          "been through this already at Lumen, so looping him in.\n\nDev", cc=[me])
    b.msg("ravi.iyer@stripe.com", ["dev@orbital.dev"], _dt(base, 39, r), "Re: Stripe x Orbital: billing integration",
          "Thanks Dev, looks good. We'll review internally.\n\nRavi", cc=[me, "omar.haddad@stripe.com"])
    b.msg("aisha@acme-robotics.com", ["claire.dubois@stripe.com"], _dt(base, 90, r),
          "Acme payments migration", "Claire — quick heads up on the migration timeline. Kush cc'd for context.\n\nAisha", cc=[me])
    b.msg("maya@northwind.io", ["diego.santos@notion.so"], _dt(base, 25, r),
          "Northwind + Notion workspace rollout", "Diego, here's the rollout plan. Kush is on cc — he's done a similar rollout.\n\nMaya", cc=[me])

    # --- Recent threads carrying the 5 open commitments ---
    b.thread(me, "maya@northwind.io", "Pricing deck v3", "the pricing deck", 4, 3,
             extras={0: "I'll send over the updated pricing deck by Friday.",
                     1: "Great — I'll route it to our finance lead once it lands."})
    b.thread("dev@orbital.dev", me, "Infra lead intro", "the infra intro", 6, 2,
             extras={0: "I'll intro you to our infra lead next week, she's been asking about Lumen."})
    b.thread("jonas@bluefin.capital", me, "Term sheet redline", "the term sheet", 3, 2,
             extras={0: "I'll send the term sheet redline by Thursday so we can close before the 15th."})
    b.thread(me, "aisha@acme-robotics.com", "Pilot kickoff", "the pilot", 7, 3,
             extras={0: "I'll get you the API docs before the pilot kicks off.",
                     2: "Can you confirm who from Acme will be on the kickoff call?"})
    b.thread("sofia@lumenlabs.io", me, "Board update draft", "the board update", 2, 2,
             extras={0: "Can you send me the metrics slide by tomorrow? I'll fold it into the deck."})

    b.msgs.sort(key=lambda m: m["date"])

    # --- Calendar: 17 past meetings with strong/colleague ties, 3 upcoming ---
    meetings = []
    pool = strong + ["ben@lumenlabs.io", "yuki@lumenlabs.io"]
    for i in range(17):
        who = r.sample(pool, r.choice([1, 1, 2, 3]))
        subject = r.choice(TOPICS["strong"])[0]
        start = _dt(base, r.uniform(3, 500), r).replace(minute=0, second=0, microsecond=0)
        meetings.append((f"mtg-{i:03d}", subject, start, start + timedelta(minutes=30), [me] + who))
    # cold ties had meetings too, back when things were warm
    for i, e in enumerate(cold):
        start = _dt(base, r.uniform(160, 240), r).replace(minute=0, second=0, microsecond=0)
        meetings.append((f"mtg-cold-{i}", r.choice(TOPICS["cold"])[0], start, start + timedelta(minutes=30), [me, e]))
    upcoming = [
        ("mtg-next-1", "Northwind x Lumen — Q4 roadmap sync", 2, [me, "maya@northwind.io", "hannah@northwind.io", "sofia@lumenlabs.io"]),
        ("mtg-next-2", "Stripe partner program — first call", 5, [me, "priya.nair@stripe.com", "dev@orbital.dev"]),
        ("mtg-next-3", "Bluefin quarterly check-in", 9, [me, "jonas@bluefin.capital", "amara@bluefin.capital"]),
    ]
    for uid, title, days, who in upcoming:
        start = (base + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
        meetings.append((uid, title, start, start + timedelta(minutes=45), who))
    return b.msgs, meetings


def write_mbox(msgs, path: Path):
    if path.exists():
        path.unlink()
    box = mailbox.mbox(str(path))
    for m in msgs:
        em = EmailMessage()
        em["From"] = email.utils.formataddr((NAME[m["sender"]], m["sender"]))
        em["To"] = ", ".join(email.utils.formataddr((NAME[a], a)) for a in m["to"])
        if m["cc"]:
            em["Cc"] = ", ".join(email.utils.formataddr((NAME[a], a)) for a in m["cc"])
        em["Subject"] = m["subject"]
        em["Date"] = email.utils.format_datetime(m["date"])
        em["Message-ID"] = m["id"]
        if m["reply_to"]:
            em["In-Reply-To"] = m["reply_to"]
        em.set_content(m["body"])
        box.add(em)
    box.flush()
    box.close()


def write_ics(meetings, path: Path):
    cal = Calendar()
    cal.add("prodid", "-//PeopleGraph synthetic//EN")
    cal.add("version", "2.0")
    for uid, title, start, end, who in meetings:
        ev = Event()
        ev.add("uid", uid)
        ev.add("summary", title)
        ev.add("dtstart", start)
        ev.add("dtend", end)
        org = vCalAddress(f"mailto:{who[0]}")
        org.params["cn"] = vText(NAME[who[0]])
        ev["organizer"] = org
        for a in who:
            att = vCalAddress(f"mailto:{a}")
            att.params["cn"] = vText(NAME[a])
            att.params["partstat"] = vText("ACCEPTED")
            ev.add("attendee", att, encode=0)
        cal.add_component(ev)
    path.write_bytes(cal.to_ical())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    msgs, meetings = build(base)
    write_mbox(msgs, out / "inbox.mbox")
    write_ics(meetings, out / "calendar.ics")
    people = {m["sender"] for m in msgs} | {a for m in msgs for a in m["to"] + m["cc"]}
    print(f"wrote {len(msgs)} emails, {len(meetings)} meetings, {len(people)} people -> {out}")
    print(f"owner: {ME[0]} <{ME[1]}>")


if __name__ == "__main__":
    main()
