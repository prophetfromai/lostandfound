from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from ..database import neo4j_connection
from neo4j import Driver

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

class ParameterModel(BaseModel):
    name: str
    type: str
    description: str
    required: bool

class ReturnModel(BaseModel):
    name: str
    type: str
    description: str

class ExampleModel(BaseModel):
    input: Dict[str, Any]
    output: Dict[str, Any]

class TemplateCreate(BaseModel):
    name: str
    description: str
    purpose: str
    version: str
    parameters: List[ParameterModel]
    returns: List[ReturnModel]
    examples: List[ExampleModel]
    cypher_query: str

class TemplateSearch(BaseModel):
    search_term: str

class TemplateCompose(BaseModel):
    templates: List[str]
    composition_type: str  # "SEQUENCE" or "PARALLEL"
    name: str
    description: str

@router.post("/")
async def create_template(template: TemplateCreate):
    """Create a new template in the knowledge graph"""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            result = session.run(
                """
                MERGE (template:Template {
                    name: $name,
                    description: $description,
                    purpose: $purpose,
                    version: $version,
                    cypher_query: $cypher_query,
                    updated: datetime()
                })
                WITH template
                UNWIND $parameters as param
                MERGE (p:Parameter {
                    name: param.name,
                    type: param.type,
                    description: param.description,
                    required: param.required
                })
                MERGE (template)-[:HAS_PARAMETER]->(p)
                WITH template
                UNWIND $returns as ret
                MERGE (r:Return {
                    name: ret.name,
                    type: ret.type,
                    description: ret.description
                })
                MERGE (template)-[:RETURNS]->(r)
                WITH template
                UNWIND $examples as ex
                MERGE (e:Example {
                    input: apoc.convert.toJson(ex.input),
                    output: apoc.convert.toJson(ex.output)
                })
                MERGE (template)-[:HAS_EXAMPLE]->(e)
                RETURN template
                """,
                template.model_dump()
            )
            created = result.single()
            if not created:
                raise HTTPException(status_code=400, detail="Failed to create template")
            return {"status": "success", "template": dict(created["template"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@router.get("/search")
async def search_templates(search_term: str):
    """Search for templates based on description or purpose"""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            result = session.run(
                """
                MATCH (template:Template)
                WHERE template.description CONTAINS $search_term
                   OR template.purpose CONTAINS $search_term
                OPTIONAL MATCH (template)-[:HAS_PARAMETER]->(param:Parameter)
                OPTIONAL MATCH (template)-[:RETURNS]->(ret:Return)
                OPTIONAL MATCH (template)-[:HAS_EXAMPLE]->(ex:Example)
                RETURN template,
                       collect(DISTINCT param) as parameters,
                       collect(DISTINCT ret) as returns,
                       collect(DISTINCT ex) as examples
                ORDER BY template.updated DESC
                """,
                search_term=search_term
            )
            templates = []
            for record in result:
                template_data = dict(record["template"])
                template_data["parameters"] = [dict(p) for p in record["parameters"]]
                template_data["returns"] = [dict(r) for r in record["returns"]]
                template_data["examples"] = [dict(e) for e in record["examples"]]
                templates.append(template_data)
            return {"templates": templates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@router.post("/compose")
async def compose_templates(composition: TemplateCompose):
    """Create a new template by composing existing templates"""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # First verify all templates exist
            result = session.run(
                """
                MATCH (t:Template)
                WHERE t.name IN $template_names
                RETURN count(t) as count
                """,
                template_names=composition.templates
            )
            count = result.single()["count"]
            if count != len(composition.templates):
                raise HTTPException(status_code=400, detail="One or more templates not found")
                
            # Create the composed template
            result = session.run(
                """
                MATCH (t:Template)
                WHERE t.name IN $template_names
                WITH collect(t) as templates
                CREATE (composed:Template {
                    name: $name,
                    description: $description,
                    purpose: 'Composed template',
                    version: '1.0',
                    composition_type: $composition_type,
                    updated: datetime()
                })
                WITH composed, templates
                UNWIND range(0, size(templates)-1) as i
                WITH composed, templates[i] as template, i
                CREATE (composed)-[:COMPOSES {order: i}]->(template)
                RETURN composed
                """,
                composition.model_dump()
            )
            composed = result.single()
            if not composed:
                raise HTTPException(status_code=400, detail="Failed to compose templates")
            return {"status": "success", "template": dict(composed["composed"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@router.get("/execute/{template_name}")
async def execute_template(template_name: str, parameters: Dict[str, Any]):
    """Execute a template with the given parameters"""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # First get the template
            result = session.run(
                """
                MATCH (t:Template {name: $name})
                OPTIONAL MATCH (t)-[:HAS_PARAMETER]->(p:Parameter)
                RETURN t.cypher_query as query, collect(p) as parameters
                """,
                name=template_name
            )
            template_data = result.single()
            if not template_data:
                raise HTTPException(status_code=404, detail="Template not found")
                
            # Validate parameters
            required_params = {p["name"] for p in template_data["parameters"] if p["required"]}
            missing_params = required_params - set(parameters.keys())
            if missing_params:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Missing required parameters: {', '.join(missing_params)}"
                )
                
            # Execute the template
            result = session.run(template_data["query"], parameters)
            return {"result": [dict(record) for record in result]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close() 