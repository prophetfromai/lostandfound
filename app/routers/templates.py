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
        min_items=2
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
    """Create a new template by composing existing templates.
    
    This endpoint allows you to combine multiple existing templates into a new template.
    The composition can be done in two ways:
    
    1. SEQUENCE: Templates are executed in order, where the output of one template can potentially
       be used as input for the next template in the sequence. Templates are executed in the order
       they appear in the templates list.
       
    2. PARALLEL: Templates are executed independently and their results are combined into a single
       response. This is useful when you need to gather different types of information simultaneously.
    
    Parameters:
    - templates (List[str]): List of template names to compose. Must contain at least 2 templates.
      All templates must exist in the system before composition.
      Example: ["find_user", "count_relationships"]
      
    - composition_type (str): Must be either "SEQUENCE" or "PARALLEL". Determines how the templates
      will be executed together.
      
    - name (str): A unique name for the newly composed template. Must be 1-100 characters long.
      This name will be used to reference the composed template in future operations.
      
    - description (str): A detailed description explaining what the composed template does.
      Should clearly explain the purpose and expected outcome of combining these specific templates.
      Minimum length: 10 characters.
    
    Returns:
    - A success response with the newly created composed template details
    
    Raises:
    - 400 Error if any of the referenced templates don't exist
    - 400 Error if the composition fails
    - 500 Error if there are database connection issues
    
    Example Usage:
    ```json
    {
        "templates": ["find_user", "get_user_posts"],
        "composition_type": "SEQUENCE",
        "name": "user_with_posts",
        "description": "Retrieves a user's profile along with their recent posts in a single operation"
    }
    ```
    
    Notes:
    - The composed template will be assigned version '1.0' automatically
    - The composition order is preserved using the 'order' property in the COMPOSES relationship
    - The composed template can be executed like any other template using the /execute endpoint
    - Consider the parameter compatibility when composing templates in SEQUENCE mode
    """
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