"""FastAPI app: graph JSON for vis-network, the three demo queries, and the drafting agent.

    uvicorn peoplegraph.app:app --reload
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from . import db, llm, queries

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.driver = db.get_driver()
    app.state.driver.verify_connectivity()
    yield
    app.state.driver.close()


app = FastAPI(title="PeopleGraph CRM", lifespan=lifespan)
# The Chrome extension's content scripts call this API from mail.google.com / calendar.google.com.
app.add_middleware(CORSMiddleware, allow_origins=["https://mail.google.com", "https://calendar.google.com",
                                                  "https://contacts.google.com", "https://meet.google.com", "https://docs.google.com", "https://drive.google.com", "https://chat.google.com"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/graph")
def graph(min_warmth: int = Query(15, ge=0, le=100)):
    return queries.graph(app.state.driver, min_warmth)


@app.get("/companies")
def companies():
    return queries.companies(app.state.driver)


@app.get("/path")
def path(company: str = Query(..., description="domain (stripe.com) or name fragment (stripe)")):
    rows = queries.warm_path(app.state.driver, company)
    if rows:
        best = rows[0]
        queries.record_action(
            app.state.driver, best["contactEmail"], "query",
            content=f"Warm path to {company}: {' → '.join(best['path'])} (score {best['score']})",
            rationale="Answered a warm-path query; remembered so future drafts know this route exists.",
        )
    return {"company": company, "paths": rows}


@app.get("/cold")
def cold():
    return queries.going_cold(app.state.driver)


@app.get("/brief")
def brief():
    rows = queries.meeting_brief(app.state.driver)
    if not rows:
        return {"meeting": None, "attendees": []}
    for r in rows:
        queries.record_action(
            app.state.driver, r["email"], "brief",
            content=json.dumps({k: r[k] for k in ("meeting", "start", "topics", "theyOwe", "iOwe")}, default=str),
            rationale="Pre-meeting brief generated from thread topics and open commitments.",
        )
    return {"meeting": rows[0]["meeting"], "start": rows[0]["start"], "attendees": rows}


@app.get("/lookup")
def lookup(emails: str = Query(..., description="comma-separated emails visible on screen")):
    wanted = [e.strip().lower() for e in emails.split(",") if "@" in e][:40]
    found = {r["email"]: r for r in queries.lookup(app.state.driver, wanted)}
    return {"people": [found[e] for e in wanted if e in found], "unknown": [e for e in wanted if e not in found]}


@app.get("/person/{email}")
def person(email: str):
    ctx = queries.person_context(app.state.driver, email)
    if ctx is None:
        raise HTTPException(404, f"no Person with email {email}")
    return ctx


@app.post("/draft/{email}")
def draft(email: str):
    ctx = queries.person_context(app.state.driver, email)
    if ctx is None:
        raise HTTPException(404, f"no Person with email {email}")
    # GraphRAG: the draft is grounded by traversal around the person (threads, topics,
    # commitments, mutual contacts) AND by the agent's own prior actions on them — the graph is the memory.
    try:
        d = llm.draft_email(ctx)
    except Exception as exc:
        raise HTTPException(502, f"LLM draft failed: {exc}") from exc
    content = f"Subject: {d.subject}\n\n{d.body}"
    aid = queries.record_action(app.state.driver, email, "draft_email", content, d.rationale)
    return {"actionId": aid, "email": email, "subject": d.subject, "body": d.body,
            "rationale": d.rationale, "usedPriorActions": len(ctx["priorActions"]),
            "groundedOn": {"threads": len(ctx["threads"]), "topics": ctx["topics"],
                           "commitments": len(ctx["commitments"]), "mutual": [m["name"] for m in ctx["mutual"]]}}


@app.get("/radar")
def radar(refresh: bool = False):
    """Competition radar: every company in your network, LLM-tagged by sector, ranked by your reach into it."""
    rows = queries.radar(app.state.driver)
    untagged = [r["domain"] for r in rows if refresh or not r.get("sector")]
    if untagged:
        try:
            tags: dict[str, str] = {}
            for i in range(0, len(untagged), 60):
                tags.update(llm.classify_sectors(untagged[i:i + 60]))
            queries.set_sectors(app.state.driver, [{"domain": d, "sector": s} for d, s in tags.items()])
            for r in rows:
                r["sector"] = tags.get(r["domain"], r.get("sector"))
        except Exception as exc:  # radar still renders, just unsectored
            for r in rows:
                r.setdefault("sector", None)
            print("sector tagging failed:", exc)
    sectors: dict[str, list] = {}
    for r in rows:
        sectors.setdefault(r.get("sector") or "Other", []).append(r)
    ranked = sorted(sectors.items(), key=lambda kv: -sum(x["reach"] for x in kv[1]))
    return {"sectors": [{"sector": k, "reach": sum(x["reach"] for x in v), "companies": v} for k, v in ranked]}


@app.get("/actions")
def actions():
    return queries.actions(app.state.driver)


@app.get("/health")
def health():
    try:
        db.run(app.state.driver, "RETURN 1")
        return {"ok": True}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
