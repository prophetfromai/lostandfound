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

class ComposedQueryResult(BaseModel):
    """Model for storing results from composed template execution"""
    template_name: str
    results: List[Dict[str, Any]]
    error: Optional[str] = None


async def execute_composed_template(session, template_name: str, parameters: Dict[str, Any]) -> List[ComposedQueryResult]:
    """Execute a composed template by running its component templates in sequence or parallel,
    supporting parameter chaining from initial input and previous results."""

    result = session.run(
        """
        MATCH (composed:Template {name: $template_name})
        MATCH (composed)-[comp:COMPOSES]->(component:Template)
        OPTIONAL MATCH (component)-[:HAS_PARAMETER]->(param:Parameter)
        WITH composed, component, comp, collect({
            name: param.name,
            required: param.required,
            source: coalesce(param.source, "input")
        }) as params
        RETURN composed.composition_type as type,
               collect({
                   name: component.name,
                   query: component.cypher_query,
                   order: comp.order,
                   parameters: params
               }) as components
        """,
        template_name=template_name
    )

    record = result.single()
    if not record:
        raise HTTPException(status_code=404, detail="Composed template not found")

    composition_type = record["type"]
    components = sorted(record["components"], key=lambda x: x["order"])
    results = []
    context = parameters.copy()  # Shared context for all components

    if composition_type == "SEQUENCE":
        for component in components:
            try:
                component_params = component["parameters"]
                exec_params = {}

                for param in component_params:
                    name = param["name"]
                    source = param.get("source", "input")
                    required = param.get("required", False)

                    # Try resolving from appropriate source
                    if source == "input" and name in parameters:
                        exec_params[name] = parameters[name]
                    elif source == "previous_result":
                        # Use from most recent result, if available
                        if results and results[-1].results and name in results[-1].results[0]:
                            exec_params[name] = results[-1].results[0][name]

                    if required and name not in exec_params:
                        raise ValueError(f"Missing required parameter for {component['name']}: {name}")

                query = component["query"]

                # Special handling for relationship type substitution
                if "$relationship_type" in query and "relationship_type" in exec_params:
                    query = query.replace("$relationship_type", exec_params["relationship_type"])
                    exec_params.pop("relationship_type", None)

                query_result = session.run(query, exec_params)
                component_results = [dict(record) for record in query_result]

                if component_results:
                    context.update(component_results[0])  # update context with new values

                results.append(ComposedQueryResult(
                    template_name=component["name"],
                    results=component_results
                ))

            except Exception as e:
                results.append(ComposedQueryResult(
                    template_name=component["name"],
                    results=[],
                    error=str(e)
                ))

    else:  # PARALLEL
        for component in components:
            try:
                component_params = component["parameters"]
                exec_params = {}

                for param in component_params:
                    name = param["name"]
                    source = param.get("source", "input")
                    required = param.get("required", False)

                    if source == "input" and name in parameters:
                        exec_params[name] = parameters[name]
                    elif source == "previous_result" and name in context:
                        exec_params[name] = context[name]

                    if required and name not in exec_params:
                        raise ValueError(f"Missing required parameter for {component['name']}: {name}")

                query = component["query"]

                if "$relationship_type" in query and "relationship_type" in exec_params:
                    query = query.replace("$relationship_type", exec_params["relationship_type"])
                    exec_params.pop("relationship_type", None)

                query_result = session.run(query, exec_params)
                component_results = [dict(record) for record in query_result]

                results.append(ComposedQueryResult(
                    template_name=component["name"],
                    results=component_results
                ))

            except Exception as e:
                results.append(ComposedQueryResult(
                    template_name=component["name"],
                    results=[],
                    error=str(e)
                ))

    return results


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
    """
    Create a new template by composing existing templates.

    ---
    Human Description:
        This endpoint allows you to create a new "composed" template by combining two or more existing templates.
        You can compose them in a sequence (one after another) or in parallel (independently).
        The composed template is saved and can be used like any other template.

        How to use:
        - Send a POST request to /api/v1/templates/compose with a JSON body:
            {
                "templates": ["find_user", "count_relationships"],
                "composition_type": "SEQUENCE",
                "name": "user_relationship_analysis",
                "description": "Finds a user and counts their relationships in sequence"
            }
        - All template names must already exist.
        - The new template will be available for future use.

        On success:
            {
                "status": "success",
                "template": { ...details of the composed template... }
            }
        On error:
            {
                "detail": "One or more templates not found"
            }

    ---
    AI Agent Description:
        - Endpoint: POST /api/v1/templates/compose
        - Input:
            - templates: List[str] (min 2, must exist)
            - composition_type: "SEQUENCE" | "PARALLEL"
            - name: str
            - description: str
        - Output:
            - On success: { "status": "success", "template": { ... } }
            - On error: HTTP 400/500 with error details
        - Behavior:
            - Verifies all templates exist.
            - Creates a new template node with links to the originals and stores composition type.
            - The composed template can be used in subsequent operations (e.g., execution).
        - Constraints:
            - All template names must exist.
            - At least two templates required.
            - Name and description must be provided.
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
            # Check if this is a composed template
            result = session.run(
                """
                MATCH (t:Template {name: $template_name})
                RETURN exists((t)-[:COMPOSES]->()) as is_composed,
                       t.cypher_query as query
                """,
                template_name=template_name
            )
            template_info = result.single()
            if not template_info:
                raise HTTPException(status_code=404, detail="Template not found")

            if template_info["is_composed"]:
                # Handle composed template execution
                results = await execute_composed_template(session, template_name, parameters)
                return {
                    "composed_results": [result.dict() for result in results]
                }
            else:
                # Execute single template
                if not template_info["query"]:
                    raise HTTPException(status_code=400, detail="Template has no query defined")

                # Handle specific template queries
                if template_name == "find_user_items":
                    # Specific handling for 'find_user_items' template
                    cypher_query = "MATCH (u:User {id: $user_id})-[:OWNS]->(i:Item) RETURN i as items"
                    result = session.run(cypher_query, parameters)
                    records = [dict(record) for record in result]
                    if not records:
                        return {"result": [], "message": "No items found for this user"}
                    return {"result": records}
                
                elif template_name == "find_user_relationships":
                    # Handle 'find_user_relationships' specific logic with dynamic relationship type
                    relationship_type = parameters.get("relationship_type")
                    if not relationship_type:
                        raise HTTPException(status_code=400, detail="Relationship type is required")

                    # Dynamic query generation based on relationship type
                    cypher_query = f"""
                        MATCH (u:User {{id: $user_id}})
                        OPTIONAL MATCH (u)-[r:{relationship_type}]->(other:User)
                        RETURN u as user, collect(r) as relationships, count(r) as count
                    """
                    result = session.run(cypher_query, parameters)
                    records = [dict(record) for record in result]
                    if not records:
                        return {"result": [], "message": "No relationships found for this user"}
                    return {"result": records}

                # Execute the generic template query
                result = session.run(template_info["query"], parameters)
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

@router.get("/{template_name}")
async def get_template(template_name: str):
    """
    Get a single template by name with all its details.
    If the template is a composed template, it will include information about its components.
    
    Args:
        template_name: The name of the template to retrieve
        
    Returns:
        dict: The template details including parameters, returns, examples, and composition info if applicable
    """
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # First check if this is a composed template
            result = session.run(
                """
                MATCH (t:Template {name: $template_name})
                OPTIONAL MATCH (t)-[comp:COMPOSES]->(component:Template)
                WITH t, collect({
                    name: component.name,
                    order: comp.order
                }) as components
                RETURN t, components, size(components) > 0 as is_composed
                """,
                template_name=template_name
            )
            record = result.single()
            if not record:
                raise HTTPException(status_code=404, detail="Template not found")
                
            template_data = dict(record["t"])
            is_composed = record["is_composed"]
            
            if is_composed:
                # Get the composition type and components
                template_data["composition_type"] = template_data.get("composition_type", "SEQUENCE")
                template_data["components"] = sorted(record["components"], key=lambda x: x["order"])
                
                # Get details for each component
                for component in template_data["components"]:
                    component_result = session.run(
                        """
                        MATCH (t:Template {name: $name})
                        OPTIONAL MATCH (t)-[:HAS_PARAMETER]->(param:Parameter)
                        OPTIONAL MATCH (t)-[:RETURNS]->(ret:Return)
                        RETURN collect(DISTINCT param) as parameters,
                               collect(DISTINCT ret) as returns
                        """,
                        name=component["name"]
                    )
                    component_details = component_result.single()
                    component["parameters"] = [dict(p) for p in component_details["parameters"]]
                    component["returns"] = [dict(r) for r in component_details["returns"]]
            else:
                # Get regular template details
                result = session.run(
                    """
                    MATCH (template:Template {name: $template_name})
                    OPTIONAL MATCH (template)-[:HAS_PARAMETER]->(param:Parameter)
                    OPTIONAL MATCH (template)-[:RETURNS]->(ret:Return)
                    OPTIONAL MATCH (template)-[:HAS_EXAMPLE]->(ex:Example)
                    RETURN collect(DISTINCT param) as parameters,
                           collect(DISTINCT ret) as returns,
                           collect(DISTINCT ex) as examples
                    """,
                    template_name=template_name
                )
                details = result.single()
                template_data["parameters"] = [dict(p) for p in details["parameters"]]
                template_data["returns"] = [dict(r) for r in details["returns"]]
                template_data["examples"] = [dict(e) for e in details["examples"]]
            
            # Clean up the 'updated' field
            if "updated" in template_data:
                template_data["updated"] = serialize_neo4j_datetime(template_data["updated"])
            
            return template_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@router.delete("/{template_name}")
async def delete_template(template_name: str):
    """
    Delete a template by name.
    
    Args:
        template_name: The name of the template to delete
        
    Returns:
        dict: Status message indicating success or failure
    """
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # First check if template exists
            result = session.run(
                """
                MATCH (t:Template {name: $template_name})
                RETURN t
                """,
                template_name=template_name
            )
            if not result.single():
                raise HTTPException(status_code=404, detail="Template not found")
                
            # Delete the template and all its relationships
            result = session.run(
                """
                MATCH (t:Template {name: $template_name})
                OPTIONAL MATCH (t)-[r]->(n)
                DELETE r, t
                RETURN count(t) as deleted_count
                """,
                template_name=template_name
            )
            deleted_count = result.single()["deleted_count"]
            if deleted_count == 0:
                raise HTTPException(status_code=500, detail="Failed to delete template")
                
            return {
                "status": "success",
                "message": f"Template '{template_name}' deleted successfully"
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close() 