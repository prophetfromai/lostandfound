from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from ..database import neo4j_connection
from neo4j import Driver
import json

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

class ParameterModel(BaseModel):
    name: str = Field(
        description="The name of the parameter that will be used in the Cypher query",
        examples=["user_id", "relationship_type", "start_date"]
    )
    type: str = Field(
        description="The expected data type of the parameter",
        examples=["string", "integer", "datetime", "list[string]", "dict"],
    )
    description: str = Field(
        description="Detailed description of what this parameter is used for and any constraints",
        examples=["Unique identifier for the user", "Type of relationship to search for"]
    )
    required: bool = Field(
        description="Indicates if this parameter must be provided when executing the template",
        examples=[True, False]
    )

class ReturnModel(BaseModel):
    name: str = Field(
        description="The name of the returned field in the query result",
        examples=["user", "relationships", "count"]
    )
    type: str = Field(
        description="The data type of the returned value",
        examples=["Node", "Relationship", "integer", "list[Node]", "Path"]
    )
    description: str = Field(
        description="Detailed description of what this return value represents",
        examples=["The matched user node with all properties", "Count of matching relationships"]
    )

class ExampleModel(BaseModel):
    input: Dict[str, Any] = Field(
        description="Example input parameters that can be used with this template",
        examples=[
            {"user_id": "12345", "relationship_type": "FOLLOWS"},
            {"start_date": "2024-01-01", "limit": 10}
        ]
    )
    output: Dict[str, Any] = Field(
        description="Expected output when using the corresponding example input",
        examples=[
            {"user": {"id": "12345", "name": "John"}, "relationship_count": 5},
            {"matched_paths": [{"start": "A", "end": "B"}]}
        ]
    )

class TemplateCreate(BaseModel):
    name: str = Field(
        description="Unique identifier for the template",
        examples=["find_user_relationships", "count_connections"],
        min_length=1,
        max_length=100
    )
    description: str = Field(
        description="Detailed description of what this template does",
        examples=["Finds all relationships between two users", "Counts the number of connections in a user's network"],
        min_length=10
    )
    purpose: str = Field(
        description="The business or technical purpose this template serves",
        examples=["User relationship analysis", "Network connectivity metrics"],
        min_length=5
    )
    version: str = Field(
        description="Version number of the template for tracking changes",
        examples=["1.0.0", "2.1.3"],
        pattern=r"^\d+\.\d+\.\d+$"
    )
    parameters: List[ParameterModel] = Field(
        description="List of parameters that this template accepts",
    )
    returns: List[ReturnModel] = Field(
        description="List of values that this template returns",
    )
    examples: List[ExampleModel] = Field(
        description="Example usage scenarios with inputs and expected outputs",
    )
    cypher_query: str = Field(
        description="The parameterized Cypher query that this template will execute",
        examples=[
            """
            MATCH (u:User {id: $user_id})
            OPTIONAL MATCH (u)-[r:$relationship_type]->(other:User)
            RETURN u as user, collect(r) as relationships, count(r) as count
            """
        ]
    )

class TemplateSearch(BaseModel):
    search_term: str = Field(
        description="Term to search for in template descriptions and purposes",
        examples=["user", "relationship", "network"],
        min_length=1
    )

class TemplateCompose(BaseModel):
    templates: List[str] = Field(
        description="List of template names to compose together",
        examples=[["find_user", "count_relationships"]],
        min_length=2
    )
    composition_type: Literal["SEQUENCE", "PARALLEL"] = Field(
        description="How to compose the templates - either in sequence or parallel",
        examples=["SEQUENCE", "PARALLEL"]
    )
    name: str = Field(
        description="Name for the newly composed template",
        examples=["user_relationship_analysis"],
        min_length=1,
        max_length=100
    )
    description: str = Field(
        description="Description of what the composed template does",
        examples=["Finds a user and analyzes their relationships in one operation"],
        min_length=10
    )

    class Config:
        schema_extra = {
            "example": {
                "templates": ["find_user", "count_relationships"],
                "composition_type": "SEQUENCE",
                "name": "user_relationship_analysis",
                "description": "Finds a user and counts their relationships in sequence"
            }
        }

@router.post("/")
async def create_template(template: TemplateCreate):
    """Create a new template in the knowledge graph"""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        # Convert example input/output to JSON strings before sending to Neo4j
        template_data = template.model_dump()
        for example in template_data['examples']:
            example['input'] = json.dumps(example['input'])
            example['output'] = json.dumps(example['output'])
            
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
                    input: ex.input,
                    output: ex.output
                })
                MERGE (template)-[:HAS_EXAMPLE]->(e)
                RETURN template
                """,
                template_data
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
                # Clean up the 'updated' field
                if "updated" in template_data:
                    template_data["updated"] = serialize_neo4j_datetime(template_data["updated"])
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
    """Create a new template by composing existing templates."""
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
                WHERE t.name IN $templates
                RETURN count(t) as count
                """,
                templates=composition.templates
            )
            count = result.single()["count"]
            if count != len(composition.templates):
                raise HTTPException(status_code=400, detail="One or more templates not found")
                
            # Create the composed template
            result = session.run(
                """
                MATCH (t:Template)
                WHERE t.name IN $templates
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

@router.post("/execute/{template_name}")
async def execute_template(template_name: str, parameters: Dict[str, Any]):
    """Execute a template with the given parameters."""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # First verify the template exists and get its query
            result = session.run(
                """
                MATCH (t:Template {name: $template_name})
                OPTIONAL MATCH (t)-[:HAS_PARAMETER]->(p:Parameter)
                RETURN t.cypher_query as query, collect(p) as parameters
                """,
                template_name=template_name
            )
            record = result.single()
            if not record:
                raise HTTPException(status_code=404, detail="Template not found")
                
            query = record["query"]
            template_params = record["parameters"]
            
            # Validate required parameters
            required_params = {p["name"] for p in template_params if p["required"]}
            missing_params = required_params - set(parameters.keys())
            if missing_params:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required parameters: {', '.join(missing_params)}"
                )
            
            # Execute the query
            result = session.run(query, parameters)
            records = [dict(record) for record in result]
            
            if not records:
                return {"result": [], "message": "No results found"}
                
            return {"result": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

def serialize_neo4j_datetime(neo4j_dt):
    """Convert Neo4j datetime dict to ISO 8601 string."""
    try:
        # Defensive: handle both neo4j.time.DateTime and dicts
        if hasattr(neo4j_dt, 'iso_format'):
            return neo4j_dt.iso_format()
        if isinstance(neo4j_dt, dict):
            date = neo4j_dt.get('_DateTime__date', {})
            time = neo4j_dt.get('_DateTime__time', {})
            year = date.get('_Date__year', 1970)
            month = date.get('_Date__month', 1)
            day = date.get('_Date__day', 1)
            hour = time.get('_Time__hour', 0)
            minute = time.get('_Time__minute', 0)
            second = time.get('_Time__second', 0)
            microsecond = int(time.get('_Time__nanosecond', 0) / 1000)
            dt = datetime(year, month, day, hour, minute, second, microsecond)
            return dt.isoformat()
    except Exception:
        pass
    return str(neo4j_dt)

@router.get("/")
async def get_all_templates():
    """Get all templates with their details."""
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            result = session.run(
                """
                MATCH (template:Template)
                OPTIONAL MATCH (template)-[:HAS_PARAMETER]->(param:Parameter)
                OPTIONAL MATCH (template)-[:RETURNS]->(ret:Return)
                OPTIONAL MATCH (template)-[:HAS_EXAMPLE]->(ex:Example)
                RETURN template,
                       collect(DISTINCT param) as parameters,
                       collect(DISTINCT ret) as returns,
                       collect(DISTINCT ex) as examples
                ORDER BY template.updated DESC
                """
            )
            templates = []
            for record in result:
                template_data = dict(record["template"])
                # Clean up the 'updated' field
                if "updated" in template_data:
                    template_data["updated"] = serialize_neo4j_datetime(template_data["updated"])
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