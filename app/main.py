from fastapi import FastAPI, HTTPException, APIRouter
from .database import neo4j_connection
from .cypher_templates import create_item_template
from .routers import templates
from pydantic import BaseModel
from typing import Optional, Dict, Any
from neo4j import Driver
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if not neo4j_connection.verify_connection():
        raise Exception("Failed to connect to Neo4j database")
    yield
    # Shutdown
    neo4j_connection.close()

app = FastAPI(title="Neo4j FastAPI Example", lifespan=lifespan)
api_router = APIRouter(prefix="/api/v1")


class ItemCreate(BaseModel):
    name: str
    description: str
    category: str
    location_name: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "location_name": self.location_name
        }

@api_router.get("/health")
async def health_check():
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
        driver.verify_connectivity()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@api_router.post("/initialize")
async def initialize_database():
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            # Clear existing data
            session.run("MATCH (n) DETACH DELETE n")
            
            # Create initial data
            query = """
            // Create locations
            CREATE (kitchen:Location {name: 'Kitchen', type: 'room'})
            CREATE (living_room:Location {name: 'Living Room', type: 'room'})
            CREATE (bedroom:Location {name: 'Bedroom', type: 'room'})

            // Create items
            CREATE (tv:Item {name: 'TV', description: '55 inch Smart TV', category: 'electronics'})
            CREATE (book:Item {name: 'Favorite Book', description: 'Hardcover novel', category: 'books'})
            CREATE (laptop:Item {name: 'Laptop', description: 'MacBook Pro', category: 'electronics'})

            // Create relationships
            CREATE (tv)-[:LOCATED_IN]->(living_room)
            CREATE (book)-[:LOCATED_IN]->(bedroom)
            CREATE (laptop)-[:LOCATED_IN]->(bedroom)
            """
            session.run(query)
            return {"status": "success", "message": "Database initialized with sample data"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close()

@api_router.post("/items")
async def create_item(item: ItemCreate):
    """
    Create a new item in the graph database.
    
    Args:
        item: Item data including name, description, category, and location
        
    Returns:
        dict: Status and created item details
    """
    driver: Optional[Driver] = None
    try:
        driver = neo4j_connection.connect()
        if not driver:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
            
        with driver.session() as session:
            result = session.run(
                create_item_template(item.to_dict()),
                name=item.name,
                description=item.description,
                category=item.category,
                location_name=item.location_name
            )
            created_item = result.single()
            if not created_item:
                raise HTTPException(status_code=400, detail="Failed to create item")
            return {
                "status": "success",
                "message": "Item created successfully",
                "item": dict(created_item["item"])
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if driver:
            driver.close() 


# Include all routers
app.include_router(templates.router)
app.include_router(api_router)
