"""The only two places the LLM is used: entity/commitment extraction at ingest, and drafting.

Both degrade gracefully: with no LLM_API_KEY, extraction falls back to a regex heuristic and
drafting falls back to a template, so ingest and the demo never depend on the API being up.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import config

EXTRACT_SYSTEM = (
    "You extract structured CRM signals from email threads. For each thread return 1-3 short topic "
    "labels (lowercase noun phrases, e.g. 'pricing', 'hiring', 'stripe partnership') and any explicit "
    "commitments — promises to do something. direction is 'owed_by_me' when the mailbox owner "
    "promised, 'owed_to_me' when someone else promised the owner. counterparty is the email of the "
    "other party to the commitment (from the participant list). Only include commitments that are "
    "clearly stated; never invent. Return every thread id you were given."
)

DRAFT_SYSTEM = (
    "You draft short re-engagement emails on behalf of the mailbox owner, in their voice: warm, "
    "direct, no fluff, under 120 words, plain text. Reference specific shared history from the "
    "context (threads, topics, commitments, mutual contacts). If prior agent actions exist for this "
    "person, do not repeat the same angle. Give a one-sentence rationale explaining why this angle."
)


class Commitment(BaseModel):
    text: str
    direction: Literal["owed_by_me", "owed_to_me"]
    dueHint: str = ""
    counterparty: str = ""


class ThreadExtraction(BaseModel):
    id: str
    topics: list[str] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)


class ExtractionBatch(BaseModel):
    threads: list[ThreadExtraction]


class Draft(BaseModel):
    subject: str
    body: str
    rationale: str


def _client():
    if not config.LLM_API_KEY:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=config.LLM_API_KEY)


# ---------------------------------------------------------------- extraction
def extract_batch(items: list[dict], my_email: str) -> list[ThreadExtraction]:
    """items: [{id, subject, participants:[email], snippet}] -> extractions (LLM or heuristic)."""
    client = _client()
    if client is None:
        return [_heuristic(it, my_email) for it in items]
    payload = json.dumps({"mailbox_owner": my_email, "threads": items}, ensure_ascii=False)
    resp = client.messages.parse(
        model=config.LLM_MODEL,
        max_tokens=16000,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": payload}],
        output_format=ExtractionBatch,
        output_config={"effort": "low"},
    )
    if resp.parsed_output is None:
        raise RuntimeError(f"extraction returned no parsed output (stop_reason={resp.stop_reason})")
    return resp.parsed_output.threads


_PROMISE = re.compile(r"\b(I'll|I will|I can|Will do,?)\s+([^.\n!?]{6,120})", re.IGNORECASE)
_ASK = re.compile(r"\b(Can you|Could you|Would you mind)\s+([^.\n!?]{6,120})\??", re.IGNORECASE)
_STOP = {"the", "a", "an", "re", "fwd", "fw", "and", "of", "on", "for", "to", "from", "quick", "next", "v3", "#4471"}


def _heuristic(item: dict, my_email: str) -> ThreadExtraction:
    """No-LLM fallback: topic = subject keywords; commitments = first-person promises / asks."""
    words = [w for w in re.findall(r"[a-z][a-z\-]+", item["subject"].lower()) if w not in _STOP]
    topics = [" ".join(words[:3])] if words else []
    commitments: list[Commitment] = []
    others = [p for p in item.get("participants", []) if p != my_email]
    counterparty = others[0] if others else ""
    for line in item.get("snippet", "").split("\n"):
        m = re.match(r"\[(\S+)\]\s*(.*)", line)
        if not m:
            continue
        sender, text = m.group(1), m.group(2)
        for pm in _PROMISE.finditer(text):
            commitments.append(Commitment(
                text=f"{pm.group(1)} {pm.group(2)}".strip(),
                direction="owed_by_me" if sender == my_email else "owed_to_me",
                dueHint=_due_hint(pm.group(2)),
                counterparty=counterparty if sender == my_email else sender,
            ))
        for am in _ASK.finditer(text):
            commitments.append(Commitment(
                text=f"{am.group(1)} {am.group(2)}".strip(),
                direction="owed_to_me" if sender == my_email else "owed_by_me",
                dueHint=_due_hint(am.group(2)),
                counterparty=counterparty if sender == my_email else sender,
            ))
    return ThreadExtraction(id=item["id"], topics=topics, commitments=commitments[:3])


def _due_hint(text: str) -> str:
    m = re.search(r"\b(by|before|on)\s+(\w+day|tomorrow|next week|the \d+\w*|end of \w+)", text, re.IGNORECASE)
    return m.group(0) if m else ""


# ---------------------------------------------------------------- drafting
def draft_email(context: dict) -> Draft:
    """context: {me, person, company, threads, topics, commitments, mutual, priorActions}"""
    client = _client()
    if client is None:
        return _template_draft(context)
    resp = client.messages.parse(
        model=config.LLM_MODEL,
        max_tokens=16000,
        system=DRAFT_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}],
        output_format=Draft,
    )
    if resp.parsed_output is None:
        raise RuntimeError(f"draft returned no parsed output (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def _template_draft(ctx: dict) -> Draft:
    p = ctx["person"]
    first = (p.get("name") or p["email"]).split()[0]
    topic = (ctx.get("topics") or ["what we were working on"])[0]
    last_thread = (ctx.get("threads") or [{}])[0].get("subject", "our last thread")
    mutual = ctx.get("mutual") or []
    hook = f" I was catching up with {mutual[0]['name']} and you came up." if mutual else ""
    owed = [c for c in ctx.get("commitments", []) if c.get("direction") == "owed_by_me" and c.get("status") == "open"]
    owed_line = f" Also — I still owe you: {owed[0]['text']}. On it this week." if owed else ""
    body = (
        f"Hi {first},\n\nIt's been a while since \"{last_thread}\" — I've been thinking about {topic} and "
        f"wanted to see where things landed on your side.{hook}{owed_line}\n\n"
        f"Any chance you're free for 20 minutes in the next couple of weeks?\n\nBest,\n{ctx['me'].get('name', 'Kush')}"
    )
    return Draft(
        subject=f"Catching up on {topic}",
        body=body,
        rationale=f"No LLM key configured — template draft anchored on the last thread ({last_thread}), "
                  f"top topic ({topic}), and {len(mutual)} mutual contact(s).",
    )
