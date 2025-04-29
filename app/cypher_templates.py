from typing import Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os

# Set up Jinja2 environment
template_dir = os.path.join(os.path.dirname(__file__), 'templates', 'cypher')
env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(['cypher']),
    trim_blocks=True,
    lstrip_blocks=True
)

def create_item_template(item_data: Dict[str, Any]) -> str:
    """
    Creates a Cypher query template for adding a new item to the graph.
    
    Args:
        item_data: Dictionary containing item properties
        
    Returns:
        str: Cypher query template
    """
    template = env.get_template('create_item.cypher')
    return template.render(**item_data) 