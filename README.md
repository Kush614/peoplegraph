# PeopleGraph CRM

An agent that reads your email + calendar export, builds a living relationship graph in **Neo4j Aura**,
and answers three questions:

* **"Who's my warmest path to company X?"** — shortest path over `EMAILED` edges, scored by warmth.
* **"Which relationships are going cold?"** — real history, silent 45+ days.
* **"Brief me before my next meeting."** — per attendee: history, topics, open commitments.

Every draft, brief and query the agent produces is written back as an `AgentAction` node linked to the
person it concerns. Next time it drafts for that person it reads those first. **The graph is the memory.**

```
Gmail + Calendar APIs (read-only OAuth) ──▶ ingest (Python) ──▶ Neo4j Aura ◀── Chrome extension overlay on Gmail / Calendar
   (or a Takeout mbox + ics)                  │ LLM only here:                ◀── FastAPI + vis-network graph UI
                                              └ topics + commitments          ◀── Neo4j MCP server (Qoder / Claude Desktop / Claude Code)
```
Runtime LLM use is deliberately minimal: extraction at ingest and the re-engagement draft.
Warmth, warm paths, cold detection and briefs are pure Cypher.

## Real data: Gmail + Calendar

```bash
# one-time: Google Cloud console → enable Gmail API + Calendar API → OAuth client (Desktop) → save as data/credentials.json
.venv/bin/python scripts/fetch_google.py        # read-only scopes; last 18 months, ≤2000 mails, skips promo/social; next 30 days of events
.venv/bin/python -m peoplegraph.ingest --reset
```
The fetcher records the mailbox owner in `data/owner.txt`; leave `MY_EMAIL` blank in `.env`.

## Chrome extension (the overlay)

`extension/` is a Manifest V3 extension. Load it once: `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`.
With the API running (`uvicorn peoplegraph.app:app --port 8010`), open any email or calendar event:

* a **PeopleGraph panel** (bottom-right) shows every person on screen — warmth score, last contact, topics,
  what you owe / they owe, who introduced you, how many prior agent actions exist for them;
* names get a **warmth dot** inline;
* people silent 45+ days get a **going-cold** alert with a **Draft re-engagement** button → the draft is grounded on the
  graph (threads, commitments, mutual contacts, prior AgentActions), written back as an `AgentAction`, and opens in Gmail compose.

The popup lets you change the API base URL (default `http://127.0.0.1:8010`).

## Quickstart with synthetic data (10 minutes)

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env            # fill in Aura URI/password (+ LLM_API_KEY, optional)
# or provision from the CLI: aura instance create --name peoplegraph --type free-db --await --output json

.venv/bin/python scripts/generate_synthetic.py       # data/inbox.mbox + data/calendar.ics
.venv/bin/python -m peoplegraph.ingest --dry-run     # parse + aggregate, no DB
.venv/bin/python -m peoplegraph.ingest --reset       # full ingest into Aura (~20s)
.venv/bin/uvicorn peoplegraph.app:app --port 8010    # http://127.0.0.1:8010  (8000 is taken on this Mac)
```

Real Gmail data: drop a Google Takeout `inbox.mbox` and Calendar `.ics` into `data/` and set `MY_EMAIL`.
Without `LLM_API_KEY`, ingest uses a regex heuristic for topics/commitments and `/draft` returns a
template — the whole demo still runs.

## Data model

| Node | Key | Notes |
|---|---|---|
| `Person` | `email` | `name, firstSeen, lastSeen, isMe, warmth (0–100)` |
| `Company` | `domain` | inferred from non-freemail domains |
| `Meeting` | `id` | from `.ics` |
| `Thread` | `id` | reply chain ∪ (normalized subject × same counterparties), split on 45-day silence |
| `Topic` | `name` | LLM extracted |
| `Commitment` | `id` | `text, direction (owed_by_me/owed_to_me), dueHint, status` |
| `AgentAction` | `id` | `type (draft_email/brief/query), content, rationale, createdAt` |

Relationships: `EMAILED{count,firstDate,lastDate}` (directed, aggregated per ordered pair), `ATTENDED`,
`WORKS_AT`, `PARTICIPATED_IN{messageCount}`, `ABOUT`, `INTRODUCED{viaThreadId,date}`, `MADE_IN`,
`OWES`, `OWED_TO`, `CONCERNS`.

**Warmth** = recency (50: full ≤2 weeks, zero at 5 months) + frequency (30) + reciprocity (20).
`cypher/warmth.cypher` — plain Cypher, no APOC.

## The three queries

They live in `cypher/` so you can paste them straight into the Aura query editor:

* `cypher/q1_warm_path.cypher` — `:param domain => 'stripe.com'`
* `cypher/q2_going_cold.cypher`
* `cypher/q3_meeting_brief.cypher`

## API

| Route | What |
|---|---|
| `GET /graph` | nodes + edges for vis-network (people coloured by warmth, companies as boxes, AgentActions as diamonds) |
| `GET /path?company=stripe.com` | Q1 (accepts a domain or a name fragment) |
| `GET /cold` | Q2 |
| `GET /brief` | Q3 for the next meeting; writes a `brief` AgentAction per attendee |
| `GET /person/{email}` | GraphRAG context bundle: threads, topics, commitments, mutual contacts, prior AgentActions |
| `POST /draft/{email}` | draft re-engagement email grounded on that bundle; writes a `draft_email` AgentAction |
| `GET /radar` | competition radar: every company in your network, LLM-tagged by sector, ranked by your reach (people, warmth) |
| `GET /actions` | agent memory |

## MCP (the live natural-language demo)

```bash
mcp/register.sh claude-code     # registers the official mcp-neo4j-cypher server with Claude Code
mcp/register.sh                 # prints a config block for Claude Desktop / Qoder
```
Prompts and expected answers: `mcp/DEMO_PROMPTS.md`.

## Synthetic storyline (`scripts/generate_synthetic.py`, seed 42, dates relative to today)

39 people / 8 companies (Stripe + Notion as targets); 5 strong reciprocal ties; 4 ties hot ~6 months
ago and silent since; 3 planted introductions; 5 open commitments in recent threads; 24 meetings,
3 upcoming. Stripe is reachable only through Dev Raman (Orbital) and Aisha Bello (Acme).
