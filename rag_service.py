"""
RAG (Retrieval-Augmented Generation) Service.
Uses ChromaDB for vector storage and sentence-transformers for embeddings.
Implements grounding to ensure answers are anchored to retrieved context.
"""
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import RAGResult, RAGSource

logger = get_logger(__name__)


class RAGService:
    """
    Production RAG service with:
    - ChromaDB persistent vector store
    - Sentence-transformer embeddings
    - Grounding validation (answer must cite retrieved context)
    - Confidence scoring
    """

    def __init__(self):
        self._chroma_client = None
        self._collection = None
        self._embedding_fn = None
        self._initialized = False

    def _lazy_init(self):
        """Lazy-load heavy dependencies to speed up startup."""
        if self._initialized:
            return

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            os.makedirs(settings.CHROMA_PERSIST_DIR, exist_ok=True)

            self._chroma_client = chromadb.PersistentClient(
                path=settings.CHROMA_PERSIST_DIR
            )
            self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=settings.EMBEDDING_MODEL
            )
            self._collection = self._chroma_client.get_or_create_collection(
                name="workday_integration_knowledge",
                embedding_function=self._embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            self._initialized = True
            logger.info(
                "RAG service initialized",
                extra={"collection_count": self._collection.count()},
            )
        except ImportError as e:
            logger.error(f"RAG dependencies not installed: {e}")
            raise
        except Exception as e:
            logger.error(f"RAG initialization failed: {e}")
            raise

    async def ingest_document(
        self,
        title: str,
        content: str,
        category: str,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """Ingest a document into the vector store with chunking."""
        self._lazy_init()
        doc_id = doc_id or str(uuid.uuid4())

        chunks = self._chunk_text(content)
        chunk_ids = []
        chunk_docs = []
        chunk_metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_chunk_{i}"
            chunk_ids.append(chunk_id)
            chunk_docs.append(chunk)
            chunk_metadatas.append({
                "doc_id": doc_id,
                "title": title,
                "category": category,
                "tags": json.dumps(tags or []),
                "chunk_index": i,
                "total_chunks": len(chunks),
                **(metadata or {}),
            })

        self._collection.upsert(
            ids=chunk_ids,
            documents=chunk_docs,
            metadatas=chunk_metadatas,
        )
        logger.info(f"Ingested document '{title}' as {len(chunks)} chunks (id={doc_id})")
        return doc_id

    async def retrieve(
        self, query: str, top_k: Optional[int] = None, category_filter: Optional[str] = None
    ) -> List[RAGSource]:
        """Retrieve relevant documents for a query."""
        self._lazy_init()

        k = top_k or settings.RAG_TOP_K
        where = {"category": category_filter} if category_filter else None

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(k, max(1, self._collection.count())),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning(f"ChromaDB query failed: {e}")
            return []

        sources = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # ChromaDB cosine distance → similarity: 1 - distance
            similarity = round(1.0 - dist, 4)
            if similarity >= settings.RAG_SIMILARITY_THRESHOLD:
                sources.append(
                    RAGSource(
                        document_id=meta.get("doc_id", "unknown"),
                        source=meta.get("title", "Unknown Source"),
                        content_snippet=doc[:400],
                        similarity_score=similarity,
                        metadata={
                            "category": meta.get("category", ""),
                            "tags": json.loads(meta.get("tags", "[]")),
                        },
                    )
                )

        sources.sort(key=lambda x: x.similarity_score, reverse=True)
        return sources

    def build_grounded_prompt(
        self,
        query: str,
        sources: List[RAGSource],
        ticket_context: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Build a grounded system + user prompt.
        Grounding: instructs the model to ONLY use retrieved context
        and cite sources explicitly.
        """
        system_prompt = """You are an expert Workday HR Integration support engineer and incident analyst.

GROUNDING RULES (MUST FOLLOW):
1. Base your answer ONLY on the provided context documents below.
2. If the context does not contain enough information, explicitly say so — do NOT hallucinate.
3. Cite which source/document your answer comes from using [Source: <title>].
4. Provide a structured, actionable response with clear steps.
5. If you identify a root cause, state your confidence level (Low/Medium/High).

Your expertise covers:
- Workday integrations: EIB, Studio, Core Connectors, PECI, RaaS
- Freshservice ITSM incident management and resolution
- Authentication (OAuth2, ISU, SAML/SSO)
- Data validation, field mapping, business process errors
- Network/connectivity troubleshooting"""

        context_block = "\n\n".join(
            f"[Source {i+1}: {s.source} | Relevance: {s.similarity_score:.2f}]\n{s.content_snippet}"
            for i, s in enumerate(sources)
        )

        ticket_block = f"\n\nINCIDENT TICKET CONTEXT:\n{ticket_context}" if ticket_context else ""

        user_prompt = f"""RETRIEVED KNOWLEDGE BASE CONTEXT:
{context_block if context_block else "No relevant documents found in knowledge base."}
{ticket_block}

USER QUERY / INCIDENT DESCRIPTION:
{query}

Provide:
1. ROOT CAUSE ANALYSIS
2. RESOLUTION STEPS (numbered, actionable)
3. WORKAROUND (if applicable)
4. CONFIDENCE LEVEL and sources cited
"""
        return system_prompt, user_prompt

    def calculate_grounding_confidence(
        self, sources: List[RAGSource], response: str
    ) -> Tuple[float, bool]:
        """
        Calculate grounding score: how well the response is anchored to sources.
        Returns (confidence, is_grounded).
        """
        if not sources:
            return 0.3, False

        avg_similarity = sum(s.similarity_score for s in sources) / len(sources)
        top_similarity = sources[0].similarity_score if sources else 0.0

        # Check if response cites sources
        cites_sources = "[source" in response.lower() or "source:" in response.lower()
        has_disclaimer = "not contain" in response.lower() or "cannot find" in response.lower()

        base_confidence = (avg_similarity * 0.4 + top_similarity * 0.6)
        if cites_sources:
            base_confidence = min(0.98, base_confidence + 0.1)
        if has_disclaimer:
            base_confidence *= 0.7

        is_grounded = top_similarity >= settings.RAG_SIMILARITY_THRESHOLD and len(sources) >= 1
        return round(base_confidence, 3), is_grounded

    async def ingest_knowledge_base_files(self):
        """Load all files from the knowledge_base directory into ChromaDB."""
        kb_path = Path(settings.KNOWLEDGE_BASE_DIR)
        if not kb_path.exists():
            logger.warning(f"Knowledge base directory not found: {kb_path}")
            return

        ingested = 0
        for file_path in kb_path.rglob("*"):
            if file_path.suffix in {".txt", ".md"}:
                try:
                    content = file_path.read_text(encoding="utf-8")
                    category = file_path.parent.name if file_path.parent != kb_path else "general"
                    await self.ingest_document(
                        title=file_path.stem.replace("_", " ").title(),
                        content=content,
                        category=category,
                        doc_id=str(file_path.relative_to(kb_path)),
                    )
                    ingested += 1
                except Exception as e:
                    logger.warning(f"Failed to ingest {file_path}: {e}")

        logger.info(f"Knowledge base ingestion complete: {ingested} files")

    def get_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics."""
        try:
            self._lazy_init()
            count = self._collection.count()
            return {"total_chunks": count, "initialized": self._initialized}
        except Exception:
            return {"total_chunks": 0, "initialized": False}

    def _chunk_text(self, text: str) -> List[str]:
        """Chunk text by size with overlap for better retrieval."""
        size = settings.CHUNK_SIZE
        overlap = settings.CHUNK_OVERLAP
        chunks = []
        start = 0
        while start < len(text):
            end = start + size
            chunks.append(text[start:end])
            start += size - overlap
        return [c for c in chunks if c.strip()]

    async def health_check(self) -> bool:
        try:
            self._lazy_init()
            self._collection.count()
            return True
        except Exception:
            return False
