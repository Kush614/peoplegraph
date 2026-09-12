# Natural-language prompts for the live MCP demo

The Neo4j MCP server exposes `get_neo4j_schema`, `read_neo4j_cypher`, `write_neo4j_cypher`.
The agent introspects the schema, writes Cypher, runs it against Aura live.

1. **"Who is my warmest path to anyone at Stripe?"**
   Expected (live, verified): Kush → Priya Nair (75, 1 hop — Dev introduced us 12 days ago),
   then Kush → Dev Raman → Omar Haddad (68), Kush → Aisha Bello → Claire Dubois (60).
   Point out that Priya was *unreachable* a fortnight ago — the intro edge is what created the path.

2. **"Which of my relationships are going cold? Show last contact and how much history we have."**
   Expected (live, verified): Marcus Lee (Acme), Hannah Kim (Northwind), Elena Rossi (Helio), Tom Okafor (Bluefin), Sam Park (Notion).

3. **"Brief me before my next meeting — who's attending, what do we talk about, what's still open?"**
   Expected: "Northwind x Lumen — Q4 roadmap sync" with Maya (warm), Hannah (cold!), Sofia; Maya is owed the pricing deck by Friday.

4. **"What has the agent already done for Elena Rossi?"** — reads `AgentAction` nodes: the graph is the memory.

5. **"Who introduced me to whom this year?"** — `INTRODUCED` edges.

Say this sentence out loud: *"Our agent doesn't have a context-window problem, it has a knowledge graph."*


# 3-minute demo with the extension

1. Gmail inbox open, extension panel idle. "This is where relationships go to die." (10s)
2. Click a thread → panel fills: warmth dots on the names, last-contact, what you owe them. "This is my actual network, scored from 18 months of mail — not LinkedIn." (30s)
3. Switch to Calendar, click tomorrow's meeting → every attendee briefed; one is flagged *going cold*. (30s)
4. Hit **Draft re-engagement** → draft appears with the rationale; "grounded on N threads, K commitments, J prior agent actions". Open the graph UI → the new AgentAction node is attached to that person. "Our agent doesn't have a context-window problem, it has a knowledge graph." (60s)
5. In Claude/Qoder via MCP: "Who's my warmest path to anyone at <company>?" — live Cypher against the same graph. (30s)
6. Close: "LinkedIn shows who you're connected to. PeopleGraph shows who you actually know — and it works for you, inside the tools you already live in." (15s)
