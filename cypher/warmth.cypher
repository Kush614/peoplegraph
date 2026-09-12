// Warmth 0–100 = recency (50) + frequency (30) + reciprocity (20). No APOC needed.
MATCH (me:Person {isMe:true})
MATCH (p:Person) WHERE NOT p.isMe
OPTIONAL MATCH (me)-[out:EMAILED]->(p)
OPTIONAL MATCH (p)-[inc:EMAILED]->(me)
OPTIONAL MATCH (p)-[:ATTENDED]->(m:Meeting)<-[:ATTENDED]-(me)
WITH p,
     duration.inDays(coalesce(p.lastSeen, date('2000-01-01')), date()).days AS gap,
     coalesce(out.count, 0) AS sent,
     coalesce(inc.count, 0) AS recv,
     count(DISTINCT m) AS mtgs
WITH p, sent, recv,
     CASE WHEN gap > 150 THEN 0.0 ELSE 50.0 - gap / 3.0 END AS recency,
     CASE WHEN (sent + recv) * 1.5 + mtgs * 5 > 30 THEN 30.0 ELSE (sent + recv) * 1.5 + mtgs * 5 END AS frequency
WITH p, recency, frequency,
     CASE WHEN sent = 0 OR recv = 0 THEN 0.0
          ELSE 20.0 * toFloat(CASE WHEN sent < recv THEN sent ELSE recv END)
                    / toFloat(CASE WHEN sent < recv THEN recv ELSE sent END) END AS reciprocity
SET p.warmth = toInteger(round(recency + frequency + reciprocity))
RETURN count(p) AS scored;
