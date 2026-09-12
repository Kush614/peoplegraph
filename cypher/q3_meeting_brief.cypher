// Q3 — pre-meeting brief for my next meeting: per attendee history, topics, open commitments.
MATCH (me:Person {isMe:true})-[:ATTENDED]->(m:Meeting)
WHERE m.start > datetime()
WITH me, m ORDER BY m.start ASC LIMIT 1
MATCH (m)<-[:ATTENDED]-(p:Person) WHERE NOT p.isMe
OPTIONAL MATCH (p)-[:WORKS_AT]->(co:Company)
OPTIONAL MATCH (me)-[e:EMAILED]-(p)
WITH m, p, co, sum(e.count) AS emails
OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(t:Thread)-[:ABOUT]->(topic:Topic)
OPTIONAL MATCH (p)-[:OWES]->(c:Commitment {status:'open'})
OPTIONAL MATCH (c2:Commitment {status:'open'})-[:OWED_TO]->(p)
RETURN m.title AS meeting, toString(m.start) AS start,
       p.name AS name, p.email AS email, co.name AS company, p.warmth AS warmth,
       toString(p.lastSeen) AS lastSeen, emails,
       collect(DISTINCT topic.name)[..5] AS topics,
       collect(DISTINCT c.text)[..3] AS theyOwe,
       collect(DISTINCT c2.text)[..3] AS iOwe
ORDER BY warmth DESC;
