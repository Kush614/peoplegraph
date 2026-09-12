// Q2 — relationships going cold: real history, silent for 45+ days.
MATCH (me:Person {isMe:true})-[e:EMAILED]-(p:Person)
WITH p, sum(e.count) AS history
WHERE p.lastSeen < date() - duration('P45D') AND history >= 5
OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (p)-[:OWES]->(oc:Commitment {status:'open'})
RETURN p.name AS name, p.email AS email, toString(p.lastSeen) AS lastSeen,
       duration.inDays(p.lastSeen, date()).days AS daysSilent,
       history, p.warmth AS warmth, c.name AS company, count(oc) AS openCommitments
ORDER BY history DESC, lastSeen ASC
LIMIT 10;
