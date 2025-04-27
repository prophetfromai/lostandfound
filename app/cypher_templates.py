from typing import Dict, Any

def create_item_template(item_data: Dict[str, Any]) -> str:
    """
    Creates a Cypher query template for adding a new item to the graph.
    
    Args:
        item_data: Dictionary containing item properties
        
    Returns:
        str: Cypher query template
    """
    return """
    MERGE (item:Item {
        name: $name,
        description: $description,
        category: $category
    })
    WITH item
    MATCH (location:Location {name: $location_name})
    MERGE (item)-[:LOCATED_IN]->(location)
    RETURN item
    """ 