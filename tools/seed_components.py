"""
Component Seeding Tool - Populate ChromaDB with sample components.

Reads sample components from JSON and seeds them into ChromaDB
if the database is empty.
"""

import json
import logging
from pathlib import Path

from tools.component_search import ComponentSearchTool
from schemas.component import Component

logger = logging.getLogger(__name__)


def seed_if_empty() -> None:
    """
    Seed ChromaDB with sample components if it's empty.

    This function checks if the ChromaDB collection already has components.
    If empty, it loads components from data/sample_components.json and adds them.
    """
    tool = ComponentSearchTool()

    # Check if collection is available
    if not tool._collection:
        logger.warning("ChromaDB collection not available, skipping seed")
        return

    # Get current stats
    stats = tool.get_stats()
    total_components = stats.get("total_components", 0)

    if total_components > 0:
        logger.info(f"ChromaDB already populated with {total_components} components, skipping seed")
        return

    # Load sample components from JSON
    sample_file = Path(__file__).parent.parent / "data" / "sample_components.json"

    if not sample_file.exists():
        logger.warning(f"Sample components file not found: {sample_file}")
        return

    try:
        with open(sample_file, "r") as f:
            data = json.load(f)

        components = data.get("components", [])
        logger.info(f"Loading {len(components)} sample components into ChromaDB...")

        added_count = 0
        for comp_data in components:
            # Create Component object — coerce all key_specs values to str
            raw_specs = comp_data.get("key_specs", {})
            str_specs = {k: str(v) for k, v in raw_specs.items()}

            component = Component(
                part_number=comp_data.get("part_number", ""),
                manufacturer=comp_data.get("manufacturer", ""),
                description=comp_data.get("description", ""),
                category=comp_data.get("category", "Unknown"),
                key_specs=str_specs,
                datasheet_url=comp_data.get("datasheet_url", ""),
                lifecycle_status=comp_data.get("lifecycle_status", "unknown"),
                estimated_cost_usd=comp_data.get("estimated_cost_usd"),
            )

            # Use search_text for semantic indexing (or fall back to description)
            description_text = comp_data.get("search_text", comp_data.get("description", ""))

            # Add to ChromaDB
            success = tool.add_component(component, description_text)
            if success:
                added_count += 1
                logger.debug(f"Added component: {component.part_number}")
            else:
                logger.warning(f"Failed to add component: {component.part_number}")

        logger.info(f"Successfully seeded {added_count}/{len(components)} sample components")

    except Exception as e:
        logger.error(f"Error seeding components: {e}")


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    seed_if_empty()
