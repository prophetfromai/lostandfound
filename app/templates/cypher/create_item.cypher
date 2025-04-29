MERGE (item:Item {
    name: {{ name | tojson }},
    description: {{ description | tojson }},
    category: {{ category | tojson }}
})
WITH item
MATCH (location:Location {name: {{ location_name | tojson }}})
MERGE (item)-[:LOCATED_IN]->(location)
RETURN item 