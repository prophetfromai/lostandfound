MERGE (template:Template {
    name: {{ name | tojson }},
    description: {{ description | tojson }},
    purpose: {{ purpose | tojson }},
    version: {{ version | tojson }},
    updated: datetime()
})
WITH template
UNWIND {{ parameters | tojson }} as param
MERGE (p:Parameter {
    name: param.name,
    type: param.type,
    description: param.description,
    required: param.required
})
MERGE (template)-[:HAS_PARAMETER]->(p)
WITH template
UNWIND {{ returns | tojson }} as ret
MERGE (r:Return {
    name: ret.name,
    type: ret.type,
    description: ret.description
})
MERGE (template)-[:RETURNS]->(r)
WITH template
UNWIND {{ examples | tojson }} as ex
MERGE (e:Example {
    input: ex.input,
    output: ex.output
})
MERGE (template)-[:HAS_EXAMPLE]->(e)
RETURN template 