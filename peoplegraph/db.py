"""Neo4j driver helpers + schema + warmth pass."""
from __future__ import annotations

from pathlib import Path

from neo4j import GraphDatabase, Driver

from . import config

CYPHER_DIR = config.ROOT / "cypher"


def load_cypher(name: str) -> str:
    return (CYPHER_DIR / name).read_text()


def get_driver() -> Driver:
    if not config.NEO4J_URI:
        raise RuntimeError("NEO4J_URI is not set — copy .env.example to .env and fill in your Aura credentials")
    return GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))


def run(driver: Driver, query: str, **params) -> list[dict]:
    with driver.session(database=config.NEO4J_DATABASE) as s:
        return [r.data() for r in s.run(query, **params)]


def ensure_schema(driver: Driver) -> None:
    for stmt in load_cypher("schema.cypher").split(";"):
        stmt = stmt.strip()
        if stmt:
            run(driver, stmt)


def compute_warmth(driver: Driver) -> int:
    rows = run(driver, load_cypher("warmth.cypher"))
    return rows[0]["scored"] if rows else 0


def reset(driver: Driver) -> None:
    run(driver, "MATCH (n) DETACH DELETE n")
