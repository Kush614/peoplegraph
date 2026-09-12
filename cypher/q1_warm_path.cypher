// Q1 — warmest path to anyone at a company. :param domain => 'stripe.com'
MATCH (me:Person {isMe:true})
MATCH (target:Person)-[:WORKS_AT]->(c:Company)
WHERE c.domain = $domain OR toLower(c.name) CONTAINS toLower($domain)
MATCH p = shortestPath((me)-[:EMAILED*..4]-(target))
WITH p, target, c, [x IN nodes(p) WHERE NOT x.isMe] AS hops
WITH p, target, c, hops,
     reduce(w = 0.0, n IN hops | w + coalesce(n.warmth, 0)) / size(hops) AS pathWarmth
RETURN [n IN nodes(p) | coalesce(n.name, n.email)] AS path,
       [n IN nodes(p) | n.email] AS emails,
       coalesce(target.name, target.email) AS contact,
       target.email AS contactEmail,
       c.name AS company,
       toInteger(round(pathWarmth)) AS score,
       length(p) AS hops
ORDER BY score DESC, hops ASC
LIMIT 3;
