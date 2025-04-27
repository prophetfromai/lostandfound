from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

load_dotenv()

class Neo4jConnection:
    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver = None

    def connect(self):
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