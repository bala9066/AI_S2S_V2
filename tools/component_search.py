"""
Component Search Tool - ChromaDB RAG for component datasheet search.

Searches cached component datasheets using semantic similarity.
Falls back to DigiKey/Mouser API scraping if no local match found.
"""

import logging
from typing import Optional, List
from pathlib import Path

# Optional chromadb import - may not be available due to dependency issues
# NOTE: chromadb.config.Settings is intentionally NOT imported.
# On Windows, ChromaDB 1.x Settings defines a chroma_server_nofile field
# (a Unix file-descriptor limit) that Pydantic v2 cannot infer a type for,
# causing an import-time exception.  PersistentClient works without Settings.
try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    chromadb = None  # type: ignore
    logging.warning(
        "ChromaDB not installed — component vector search disabled. "
        "Run: pip install chromadb --break-system-packages"
    )
except Exception as e:
    CHROMADB_AVAILABLE = False
    chromadb = None  # type: ignore
    logging.warning(
        "ChromaDB import failed (Windows Pydantic v2 issue?) — component search disabled. "
        "Error: %s", e
    )

from config import settings
from schemas.component import Component, ComponentSearchResult

logger = logging.getLogger(__name__)


class ComponentSearchTool:
    """
    Semantic component search using ChromaDB.

    Usage:
        tool = ComponentSearchTool()
        results = tool.search("3.3V LDO regulator 1A low noise")
    """

    def __init__(self):
        self._client: Optional[chromadb.Client] = None
        self._collection = None
        self._initialize()

    def _initialize(self):
        """Initialize ChromaDB client and collection."""
        if not CHROMADB_AVAILABLE:
            logger.warning(
                "ChromaDB not available — component vector search disabled. "
                "The pipeline will still run; component selection uses LLM knowledge instead. "
                "To enable ChromaDB: pip install chromadb"
            )
            self._client = None
            self._collection = None
            return

        try:
            # Create persist directory if needed
            persist_dir = Path(settings.chroma_persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)

            # Initialize ChromaDB client
            self._client = chromadb.PersistentClient(
                path=str(persist_dir),
            )

            # Try to use OpenAI text-embedding-3-large if key is available
            embedding_fn = None
            _placeholder_keys = {"", "sk-xxxxx", "sk-proj-xxxxx", "your-key-here"}
            if settings.openai_api_key and settings.openai_api_key.strip() not in _placeholder_keys:
                try:
                    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                    embedding_fn = OpenAIEmbeddingFunction(
                        api_key=settings.openai_api_key,
                        model_name=settings.embedding_model,  # text-embedding-3-large
                    )
                    logger.info(f"ChromaDB: using OpenAI embedding '{settings.embedding_model}'")
                except Exception as e:
                    logger.warning(f"OpenAI embedding unavailable ({e}), falling back to default (all-MiniLM-L6-v2)")
            else:
                logger.info("ChromaDB: no valid OpenAI key, using default embedding (all-MiniLM-L6-v2)")

            # Get or create collection — pass embedding fn if available
            collection_kwargs: dict = {
                "name": settings.chroma_collection_name,
                "metadata": {"hnsw:space": "cosine"},
            }
            if embedding_fn is not None:
                collection_kwargs["embedding_function"] = embedding_fn

            self._collection = self._client.get_or_create_collection(**collection_kwargs)

            logger.info(f"ChromaDB initialized: {len(self._collection.get()['ids'])} components cached")

        except Exception as e:
            logger.warning(f"ChromaDB initialization failed: {e}")
            self._client = None
            self._collection = None

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        n_results: int = 5,
        min_similarity: float = 0.6,
    ) -> List[ComponentSearchResult]:
        """
        Search for components by semantic similarity.

        Args:
            query: Natural language description of required component
            category: Optional category filter (e.g., "MCU", "Power", "Sensor")
            n_results: Maximum number of results to return
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of ComponentSearchResult with component details and similarity scores
        """
        if not self._collection:
            logger.warning("ChromaDB not available, returning empty results")
            return []

        try:
            # Query ChromaDB
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where={"category": category} if category else None,
            )

            # Parse results
            search_results = []
            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    similarity = 1 - results["distances"][0][i]  # Convert cosine distance to similarity

                    if similarity < min_similarity:
                        continue

                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                    component = Component(
                        part_number=metadata.get("part_number", ""),
                        manufacturer=metadata.get("manufacturer", ""),
                        description=metadata.get("description", ""),
                        category=metadata.get("category", "Unknown"),
                        key_specs=metadata.get("key_specs", {}),
                        datasheet_url=metadata.get("datasheet_url", ""),
                        lifecycle_status=metadata.get("lifecycle_status", "unknown"),
                        estimated_cost_usd=metadata.get("estimated_cost_usd"),
                    )

                    search_results.append(
                        ComponentSearchResult(
                            component=component,
                            relevance_score=round(similarity, 3),
                            match_reason=results["documents"][0][i] if results["documents"] else "",
                        )
                    )

            logger.info(f"Component search '{query}': {len(search_results)} results")
            return search_results

        except Exception as e:
            logger.error(f"Component search failed: {e}")
            return []

    @staticmethod
    def _flatten_metadata(component: "Component") -> dict:
        """Build ChromaDB-safe metadata (only str/int/float/bool/None)."""
        import json
        meta = {
            "part_number": component.part_number,
            "manufacturer": component.manufacturer,
            "description": component.description,
            "category": component.category,
            "datasheet_url": component.datasheet_url,
            "lifecycle_status": component.lifecycle_status,
            "estimated_cost_usd": component.estimated_cost_usd,
        }
        # Flatten key_specs dict → individual "spec_<key>" entries
        if isinstance(component.key_specs, dict):
            meta["key_specs_json"] = json.dumps(component.key_specs)
            for k, v in component.key_specs.items():
                safe_key = f"spec_{k.replace(' ', '_').lower()}"
                meta[safe_key] = str(v) if not isinstance(v, (str, int, float, bool)) else v
        return meta

    def add_component(
        self,
        component: Component,
        description_text: str,
    ) -> bool:
        """
        Add a component to the ChromaDB cache.

        Args:
            component: Component object with all details
            description_text: Full text description for semantic search (e.g., datasheet excerpt)

        Returns:
            True if successfully added
        """
        if not self._collection:
            return False

        try:
            metadata = self._flatten_metadata(component)

            # Check if already exists
            existing = self._collection.get(ids=[component.part_number])
            if existing["ids"]:
                self._collection.update(
                    ids=[component.part_number],
                    documents=[description_text],
                    metadatas=[metadata],
                )
                logger.debug(f"Updated component: {component.part_number}")
            else:
                self._collection.add(
                    ids=[component.part_number],
                    documents=[description_text],
                    metadatas=[metadata],
                )
                logger.debug(f"Added component: {component.part_number}")

            return True

        except Exception as e:
            logger.error(f"Failed to add component {component.part_number}: {e}")
            return False

    def get_by_part_number(self, part_number: str) -> Optional[Component]:
        """Get a component by its part number from cache."""
        if not self._collection:
            return None

        try:
            import json
            results = self._collection.get(ids=[part_number], include=["metadatas"])
            if results["metadatas"] and results["metadatas"][0]:
                metadata = results["metadatas"][0]
                # Reconstruct key_specs from JSON if stored that way
                key_specs = {}
                if "key_specs_json" in metadata:
                    try:
                        key_specs = json.loads(metadata["key_specs_json"])
                    except (json.JSONDecodeError, TypeError):
                        key_specs = {}
                elif "key_specs" in metadata and isinstance(metadata["key_specs"], dict):
                    key_specs = metadata["key_specs"]
                return Component(
                    part_number=metadata.get("part_number", part_number),
                    manufacturer=metadata.get("manufacturer", ""),
                    description=metadata.get("description", ""),
                    category=metadata.get("category", "Unknown"),
                    key_specs=key_specs,
                    datasheet_url=metadata.get("datasheet_url", ""),
                    lifecycle_status=metadata.get("lifecycle_status", "unknown"),
                    estimated_cost_usd=metadata.get("estimated_cost_usd"),
                )
        except Exception as e:
            logger.error(f"Failed to get component {part_number}: {e}")

        return None

    def get_stats(self) -> dict:
        """Get statistics about the component cache."""
        if not self._collection:
            return {"total_components": 0, "categories": {}}

        try:
            all_data = self._collection.get(include=["metadatas"])
            total = len(all_data["ids"])

            categories = {}
            for metadata in all_data["metadatas"] or []:
                cat = metadata.get("category", "Unknown")
                categories[cat] = categories.get(cat, 0) + 1

            return {
                "total_components": total,
                "categories": categories,
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_components": 0, "categories": {}}
