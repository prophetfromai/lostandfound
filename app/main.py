from fastapi import FastAPI, HTTPException
from .database import neo4j_connection

app = FastAPI(title="Neo4j FastAPI Example")

@app.on_event("startup")
async def startup_event():
    # Verify connection on startup
    if not neo4j_connection.verify_connection():
        raise Exception("Failed to connect to Neo4j database")

@app.on_event("shutdown")
async def shutdown_event():
    neo4j_connection.close()

@app.get("/health")
async def health_check():
    try:
        with neo4j_connection.connect() as driver:
            driver.verify_connectivity()
            return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/initialize")
async def initialize_database():
    try:
        with neo4j_connection.connect() as driver:
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