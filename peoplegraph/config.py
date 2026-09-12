import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NEO4J_URI = os.getenv("NEO4J_URI", "")
NEO4J_USER = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME") or "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE") or None  # None = server default (Aura Free names it after the instance id)

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")

DATA_DIR = ROOT / "data"

# Owner address: env wins; otherwise scripts/fetch_google.py records it; otherwise ingest infers it.
_owner_file = DATA_DIR / "owner.txt"
MY_EMAIL = (os.getenv("MY_EMAIL") or (_owner_file.read_text() if _owner_file.exists() else "")).lower().strip()
MBOX_PATH = Path(os.getenv("MBOX_PATH", DATA_DIR / "inbox.mbox"))
ICS_PATH = Path(os.getenv("ICS_PATH", DATA_DIR / "calendar.ics"))

# Domains that don't imply an employer.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com",
}

# How many of the most recent threads get sent to the LLM at ingest.
EXTRACT_THREAD_LIMIT = 50
EXTRACT_BATCH_SIZE = 10
