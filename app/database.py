from neo4j import GraphDatabase
from dotenv import load_dotenv
import os
from typing import Optional

load_dotenv()

class Neo4jConnection:
    def __init__(self):
        self.uri: str = os.getenv("NEO4J_URI", "")
        self.user: str = os.getenv("NEO4J_USER", "")
        self.password: str = os.getenv("NEO4J_PASSWORD", "")
        self.driver: Optional[GraphDatabase.driver] = None

        # Verify required environment variables
        if not all([self.uri, self.user, self.password]):
            raise ValueError("Missing required environment variables for Neo4j connection")

    def connect(self):
        if not all([self.uri, self.user, self.password]):
            raise ValueError("Missing required environment variables for Neo4j connection")
            
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password)
        )
        return self.driver

    def close(self):
        if self.driver is not None:
            self.driver.close()

    def verify_connection(self):
        try:
            with self.connect() as driver:
                driver.verify_connectivity()
                return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False

# Create a single instance to be used across the application
neo4j_connection = Neo4jConnection() 