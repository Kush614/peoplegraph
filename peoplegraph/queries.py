"""Graph queries behind the API. Q1–Q3 live in cypher/ so they can be pasted into Aura as-is."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from . import db

Q1 = db.load_cypher("q1_warm_path.cypher")
Q2 = db.load_cypher("q2_going_cold.cypher")
Q3 = db.load_cypher("q3_meeting_brief.cypher")

GRAPH = """
MATCH (p:Person)
OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
WITH collect({id: p.email, label: coalesce(p.name, p.email), kind: 'person', warmth: p.warmth,
              isMe: p.isMe, company: c.name, lastSeen: toString(p.lastSeen)}) AS people
MATCH (c:Company)
WITH people, collect({id: 'company:' + c.domain, label: c.name, kind: 'company', domain: c.domain}) AS companies
OPTIONAL MATCH (a:AgentAction)-[:CONCERNS]->(ap:Person)
WITH people, companies,
     collect(CASE WHEN a IS NULL THEN NULL ELSE
       {id: 'action:' + a.id, label: a.type, kind: 'action', type: a.type, createdAt: toString(a.createdAt),
        concerns: ap.email} END) AS actions
RETURN people, companies, [x IN actions WHERE x IS NOT NULL] AS actions
"""
EDGES = """
MATCH (a:Person)-[e:EMAILED]->(b:Person)
WITH a, b, e WHERE a.email < b.email OR NOT EXISTS { (b)-[:EMAILED]->(a) }
OPTIONAL MATCH (b)-[r:EMAILED]->(a)
WITH collect({from: a.email, to: b.email, kind: 'emailed', count: e.count + coalesce(r.count, 0)}) AS emailed
MATCH (p:Person)-[:WORKS_AT]->(c:Company)
WITH emailed, collect({from: p.email, to: 'company:' + c.domain, kind: 'works_at'}) AS works
OPTIONAL MATCH (x:Person)-[i:INTRODUCED]->(y:Person)
WITH emailed, works, collect(CASE WHEN x IS NULL THEN NULL ELSE {from: x.email, to: y.email, kind: 'introduced'} END) AS intros
OPTIONAL MATCH (act:AgentAction)-[:CONCERNS]->(p2:Person)
RETURN emailed, works, [i IN intros WHERE i IS NOT NULL] AS intros,
       collect(CASE WHEN act IS NULL THEN NULL ELSE {from: 'action:' + act.id, to: p2.email, kind: 'concerns'} END) AS concerns
"""
COMPANIES = "MATCH (c:Company)<-[:WORKS_AT]-(p) RETURN c.domain AS domain, c.name AS name, count(p) AS people ORDER BY name"

PERSON_CONTEXT = """
MATCH (me:Person {isMe:true}), (p:Person {email:$email})
OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (me)-[e:EMAILED]-(p)
WITH me, p, c, sum(e.count) AS emails
OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(t:Thread)<-[:PARTICIPATED_IN]-(me)
WITH me, p, c, emails, t ORDER BY t.lastMessageAt DESC
WITH me, p, c, emails, collect(DISTINCT {subject: t.subject, lastMessageAt: toString(t.lastMessageAt), messages: t.messageCount})[..6] AS threads
OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(:Thread)-[:ABOUT]->(topic:Topic)
WITH me, p, c, emails, threads, collect(DISTINCT topic.name)[..8] AS topics
OPTIONAL MATCH (p)-[:OWES|OWED_TO]-(cm:Commitment)
WITH me, p, c, emails, threads, topics,
     collect(DISTINCT {text: cm.text, direction: cm.direction, status: cm.status, dueHint: cm.dueHint}) AS commitments
OPTIONAL MATCH (me)-[:EMAILED]-(m:Person)-[:EMAILED]-(p) WHERE NOT m.isMe AND m <> p
WITH me, p, c, emails, threads, topics, commitments, m ORDER BY m.warmth DESC
WITH me, p, c, emails, threads, topics, commitments,
     [x IN collect(DISTINCT {name: m.name, email: m.email, warmth: m.warmth}) WHERE x.email IS NOT NULL][..5] AS mutual
OPTIONAL MATCH (me)-[:ATTENDED]->(mt:Meeting)<-[:ATTENDED]-(p)
WITH me, p, c, emails, threads, topics, commitments, mutual, count(DISTINCT mt) AS meetings
OPTIONAL MATCH (a:AgentAction)-[:CONCERNS]->(p)
WITH me, p, c, emails, threads, topics, commitments, mutual, meetings, a ORDER BY a.createdAt DESC
RETURN {name: me.name, email: me.email} AS me,
       {name: p.name, email: p.email, warmth: p.warmth, lastSeen: toString(p.lastSeen),
        firstSeen: toString(p.firstSeen), emails: emails, meetings: meetings} AS person,
       c.name AS company, threads, topics, commitments, mutual,
       collect({type: a.type, createdAt: toString(a.createdAt), content: left(a.content, 400), rationale: a.rationale})[..5] AS priorActions
"""

LOOKUP = """
UNWIND $emails AS em
MATCH (p:Person {email: em})
OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (me:Person {isMe:true})-[e:EMAILED]-(p)
WITH p, c, sum(e.count) AS emails
OPTIONAL MATCH (p)-[:OWES]->(oc:Commitment {status:'open'})
WITH p, c, emails, collect(DISTINCT oc.text)[..3] AS theyOwe
OPTIONAL MATCH (ic:Commitment {status:'open'})-[:OWED_TO]->(p)
WITH p, c, emails, theyOwe, collect(DISTINCT ic.text)[..3] AS iOwe
OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(:Thread)-[:ABOUT]->(t:Topic)
WITH p, c, emails, theyOwe, iOwe, collect(DISTINCT t.name)[..4] AS topics
OPTIONAL MATCH (a:AgentAction)-[:CONCERNS]->(p)
WITH p, c, emails, theyOwe, iOwe, topics, count(a) AS actions
OPTIONAL MATCH (x:Person)-[i:INTRODUCED]->(p) WHERE NOT x.isMe
WITH p, c, emails, theyOwe, iOwe, topics, actions, collect(DISTINCT x.name)[..2] AS introducedBy
OPTIONAL MATCH (p)-[:INTRODUCED]->(y:Person) WHERE NOT y.isMe
WITH p, c, emails, theyOwe, iOwe, topics, actions, introducedBy, collect(DISTINCT y.name)[..3] AS introduced
OPTIONAL MATCH (me:Person {isMe:true})-[:EMAILED]-(m:Person)-[:EMAILED]-(p) WHERE NOT m.isMe AND m <> p
WITH p, c, emails, theyOwe, iOwe, topics, actions, introducedBy, introduced, m ORDER BY m.warmth DESC
WITH p, c, emails, theyOwe, iOwe, topics, actions, introducedBy, introduced,
     [x IN collect(DISTINCT {name: m.name, warmth: m.warmth}) WHERE x.name IS NOT NULL][..4] AS mutual
OPTIONAL MATCH (p)-[:WORKS_AT]->(:Company)<-[:WORKS_AT]-(col:Person)-[:EMAILED]-(:Person {isMe:true})
WHERE col <> p
WITH p, c, emails, theyOwe, iOwe, topics, actions, introducedBy, introduced, mutual,
     [x IN collect(DISTINCT {name: col.name, warmth: col.warmth}) WHERE x.name IS NOT NULL][..3] AS colleagues
RETURN p.email AS email, p.name AS name, c.name AS company, p.warmth AS warmth,
       toString(p.lastSeen) AS lastSeen, duration.inDays(coalesce(p.lastSeen, date()), date()).days AS daysSilent,
       emails, topics, theyOwe, iOwe, actions, introducedBy, introduced, mutual, colleagues
"""

RADAR = """
MATCH (me:Person {isMe:true})
MATCH (c:Company)<-[:WORKS_AT]-(p:Person)
OPTIONAL MATCH (me)-[e:EMAILED]-(p)
WITH c, p, sum(e.count) AS direct
WITH c, count(p) AS people, sum(CASE WHEN direct > 0 THEN 1 ELSE 0 END) AS known,
     max(p.warmth) AS maxWarmth, avg(p.warmth) AS avgWarmth, max(p.lastSeen) AS lastContact,
     collect({name: p.name, warmth: p.warmth})[..3] AS sample
WHERE people >= 1
RETURN c.domain AS domain, c.name AS name, c.sector AS sector, people, known,
       maxWarmth, toInteger(round(avgWarmth)) AS avgWarmth, toString(lastContact) AS lastContact,
       toInteger(round(maxWarmth * 0.6 + avgWarmth * 0.2 + CASE WHEN people > 5 THEN 20 ELSE people * 4 END)) AS reach,
       [x IN sample | x.name] AS names
ORDER BY reach DESC LIMIT 120
"""

WRITE_ACTION = """
MATCH (p:Person {email:$email})
CREATE (a:AgentAction {id:$id, type:$type, content:$content, rationale:$rationale, createdAt:$createdAt})
CREATE (a)-[:CONCERNS]->(p)
RETURN a.id AS id
"""
ACTIONS = """
MATCH (a:AgentAction)-[:CONCERNS]->(p:Person)
RETURN a.id AS id, a.type AS type, a.content AS content, a.rationale AS rationale,
       toString(a.createdAt) AS createdAt, p.name AS person, p.email AS email
ORDER BY a.createdAt DESC LIMIT 20
"""


def graph(driver) -> dict:
    n = db.run(driver, GRAPH)[0]
    e = db.run(driver, EDGES)[0]
    return {"nodes": n["people"] + n["companies"] + n["actions"],
            "edges": e["emailed"] + e["works"] + e["intros"] + [c for c in e["concerns"] if c]}


def warm_path(driver, company: str) -> list[dict]:
    return db.run(driver, Q1, domain=company.strip().lower())


def going_cold(driver) -> list[dict]:
    return db.run(driver, Q2)


def meeting_brief(driver) -> list[dict]:
    return db.run(driver, Q3)


def companies(driver) -> list[dict]:
    return db.run(driver, COMPANIES)


def person_context(driver, email: str) -> dict | None:
    rows = db.run(driver, PERSON_CONTEXT, email=email.lower())
    if not rows or rows[0]["person"]["email"] is None:
        return None
    ctx = rows[0]
    ctx["commitments"] = [c for c in ctx["commitments"] if c.get("text")]
    ctx["priorActions"] = [a for a in ctx["priorActions"] if a.get("type")]
    return ctx


def radar(driver) -> list[dict]:
    return db.run(driver, RADAR)


def set_sectors(driver, rows: list[dict]) -> None:
    db.run(driver, "UNWIND $rows AS r MATCH (c:Company {domain: r.domain}) SET c.sector = r.sector", rows=rows)


def lookup(driver, emails: list[str]) -> list[dict]:
    return db.run(driver, LOOKUP, emails=[e.lower() for e in emails])


def record_action(driver, email: str, type_: str, content: str, rationale: str) -> str:
    now = datetime.now(timezone.utc)
    aid = hashlib.sha1(f"{email}|{type_}|{now.isoformat()}".encode()).hexdigest()[:16]
    db.run(driver, WRITE_ACTION, email=email.lower(), id=aid, type=type_, content=content,
           rationale=rationale, createdAt=now)
    return aid


def actions(driver) -> list[dict]:
    return db.run(driver, ACTIONS)
