MATCH (template:Template)
WHERE template.description CONTAINS {{ search_term | tojson }}
   OR template.purpose CONTAINS {{ search_term | tojson }}
OPTIONAL MATCH (template)-[:HAS_PARAMETER]->(param:Parameter)
OPTIONAL MATCH (template)-[:RETURNS]->(ret:Return)
OPTIONAL MATCH (template)-[:HAS_EXAMPLE]->(ex:Example)
RETURN template,
       collect(DISTINCT param) as parameters,
       collect(DISTINCT ret) as returns,
       collect(DISTINCT ex) as examples
ORDER BY template.updated DESC 