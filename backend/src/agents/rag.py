"""
RAG (Retrieval Augmented Generation) service for semantic search over hockey content.

Features:
- Semantic search using sentence embeddings (all-MiniLM-L6-v2)
- Hybrid search combining keyword and semantic matching
- Query-type-aware retrieval strategies
- Re-ranking for improved relevance
- Source citations with confidence scores
"""
from enum import Enum
from dataclasses import dataclass
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import numpy as np
import re

logger = structlog.get_logger()

# Using a small, fast model that runs well locally
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class RetrievalStrategy(Enum):
    """Different retrieval strategies for different query types."""
    SEMANTIC = "semantic"           # Pure embedding similarity
    KEYWORD = "keyword"             # Full-text search
    HYBRID = "hybrid"               # Combine semantic + keyword
    CONCEPT = "concept"             # For explainer queries - prioritize definitions
    RECENCY = "recency"             # For news/recent events - prioritize recent docs


@dataclass
class RetrievedDocument:
    """A document retrieved from the knowledge base with citation info."""
    id: int
    title: str | None
    source: str | None
    content: str
    url: str | None
    similarity: float
    retrieval_method: str
    citation: str  # Formatted citation for response

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
            "url": self.url,
            "similarity": round(self.similarity, 3),
            "retrieval_method": self.retrieval_method,
            "citation": self.citation,
        }


class RAGService:
    """Service for embedding and retrieving documents."""

    def __init__(self):
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        """Lazy load the embedding model."""
        if self._model is None:
            logger.info("loading_embedding_model", model=EMBEDDING_MODEL)
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a text string."""
        embedding = self.model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    async def add_document(
        self,
        db: AsyncSession,
        content: str,
        title: str | None = None,
        source: str | None = None,
        url: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Add a document to the database with its embedding."""
        embedding = self.embed(content)

        result = await db.execute(
            text("""
                INSERT INTO documents (title, source, content, url, embedding, metadata)
                VALUES (:title, :source, :content, :url, :embedding, :metadata)
                RETURNING id
            """),
            {
                "title": title,
                "source": source,
                "content": content,
                "url": url,
                "embedding": str(embedding),
                "metadata": metadata,
            },
        )
        doc_id = result.scalar_one()
        await db.commit()

        logger.info("document_added", doc_id=doc_id, title=title)
        return doc_id

    def determine_strategy(self, query: str, query_type: str | None = None) -> RetrievalStrategy:
        """
        Determine the best retrieval strategy based on query characteristics.

        Different query types benefit from different retrieval approaches:
        - "What is xG?" -> CONCEPT (prioritize definitions)
        - "Latest news on McDavid" -> RECENCY
        - "Advanced stats articles" -> SEMANTIC
        - "WAR calculation" -> HYBRID (specific term + context)
        """
        query_lower = query.lower()

        # Explicit explainer queries -> concept-focused retrieval
        if query_type == "explainer" or any(phrase in query_lower for phrase in [
            "what is", "what are", "explain", "definition", "how does", "how do"
        ]):
            return RetrievalStrategy.CONCEPT

        # News/recent queries -> recency-focused
        if any(phrase in query_lower for phrase in [
            "latest", "recent", "news", "today", "this week", "update"
        ]):
            return RetrievalStrategy.RECENCY

        # Technical terms that need exact matching -> hybrid
        technical_terms = ["war", "xg", "corsi", "fenwick", "pdo", "gsax", "hdcf"]
        if any(term in query_lower for term in technical_terms):
            return RetrievalStrategy.HYBRID

        # Default to semantic search
        return RetrievalStrategy.SEMANTIC

    async def search(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.3,
        strategy: RetrievalStrategy | None = None,
        query_type: str | None = None,
    ) -> list[dict]:
        """
        Search for documents similar to the query.

        Uses cosine similarity via pgvector with strategy-specific optimizations.
        """
        # Determine strategy if not specified
        if strategy is None:
            strategy = self.determine_strategy(query, query_type)

        logger.info("rag_search_start", query=query[:50], strategy=strategy.value)

        # Execute retrieval based on strategy
        if strategy == RetrievalStrategy.HYBRID:
            documents = await self._hybrid_search(db, query, limit, min_similarity)
        elif strategy == RetrievalStrategy.CONCEPT:
            documents = await self._concept_search(db, query, limit, min_similarity)
        elif strategy == RetrievalStrategy.RECENCY:
            documents = await self._recency_search(db, query, limit, min_similarity)
        else:  # SEMANTIC (default)
            documents = await self._semantic_search(db, query, limit, min_similarity)

        # Re-rank results for better relevance
        documents = self._rerank_results(documents, query)

        logger.info("rag_search_complete", query=query[:50], results=len(documents))

        # Convert to dict format for backwards compatibility
        return [doc.to_dict() for doc in documents]

    async def _semantic_search(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedDocument]:
        """Pure semantic search using embeddings."""
        query_embedding = self.embed(query)

        result = await db.execute(
            text("""
                SELECT
                    id, title, source, content, url,
                    1 - (embedding <=> :embedding) as similarity
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :embedding
                LIMIT :limit
            """),
            {"embedding": str(query_embedding), "limit": limit},
        )

        return [
            RetrievedDocument(
                id=row.id,
                title=row.title,
                source=row.source,
                content=row.content,
                url=row.url,
                similarity=row.similarity,
                retrieval_method="semantic",
                citation=self._format_citation(row.title, row.source, row.url),
            )
            for row in result.fetchall()
            if row.similarity >= min_similarity
        ]

    async def _hybrid_search(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedDocument]:
        """Combine semantic and keyword search."""
        query_embedding = self.embed(query)

        # Extract keywords for text matching
        keywords = self._extract_keywords(query)
        keyword_pattern = "|".join(re.escape(k) for k in keywords) if keywords else ""

        result = await db.execute(
            text("""
                SELECT
                    id, title, source, content, url,
                    1 - (embedding <=> :embedding) as semantic_sim,
                    CASE
                        WHEN content ~* :pattern THEN 0.2
                        WHEN title ~* :pattern THEN 0.3
                        ELSE 0
                    END as keyword_boost
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY (1 - (embedding <=> :embedding)) + CASE
                    WHEN content ~* :pattern THEN 0.2
                    WHEN title ~* :pattern THEN 0.3
                    ELSE 0
                END DESC
                LIMIT :limit
            """),
            {
                "embedding": str(query_embedding),
                "pattern": keyword_pattern,
                "limit": limit,
            },
        )

        return [
            RetrievedDocument(
                id=row.id,
                title=row.title,
                source=row.source,
                content=row.content,
                url=row.url,
                similarity=row.semantic_sim + row.keyword_boost,
                retrieval_method="hybrid",
                citation=self._format_citation(row.title, row.source, row.url),
            )
            for row in result.fetchall()
            if row.semantic_sim >= min_similarity * 0.8  # Lower threshold for hybrid
        ]

    async def _concept_search(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedDocument]:
        """Search optimized for concept/definition queries."""
        query_embedding = self.embed(query)

        # Boost documents that contain definition-like patterns
        result = await db.execute(
            text("""
                SELECT
                    id, title, source, content, url,
                    1 - (embedding <=> :embedding) as semantic_sim,
                    CASE
                        WHEN content ~* '(is defined as|refers to|measures|calculates)' THEN 0.15
                        WHEN content ~* '(what is|definition|explanation)' THEN 0.1
                        ELSE 0
                    END as concept_boost
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY (1 - (embedding <=> :embedding)) + CASE
                    WHEN content ~* '(is defined as|refers to|measures|calculates)' THEN 0.15
                    WHEN content ~* '(what is|definition|explanation)' THEN 0.1
                    ELSE 0
                END DESC
                LIMIT :limit
            """),
            {"embedding": str(query_embedding), "limit": limit},
        )

        return [
            RetrievedDocument(
                id=row.id,
                title=row.title,
                source=row.source,
                content=row.content,
                url=row.url,
                similarity=row.semantic_sim + row.concept_boost,
                retrieval_method="concept",
                citation=self._format_citation(row.title, row.source, row.url),
            )
            for row in result.fetchall()
            if row.semantic_sim >= min_similarity
        ]

    async def _recency_search(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedDocument]:
        """Search with recency boost for news/updates."""
        query_embedding = self.embed(query)

        result = await db.execute(
            text("""
                SELECT
                    id, title, source, content, url, published_at,
                    1 - (embedding <=> :embedding) as semantic_sim
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY
                    (1 - (embedding <=> :embedding)) +
                    CASE
                        WHEN published_at > NOW() - INTERVAL '7 days' THEN 0.2
                        WHEN published_at > NOW() - INTERVAL '30 days' THEN 0.1
                        ELSE 0
                    END DESC
                LIMIT :limit
            """),
            {"embedding": str(query_embedding), "limit": limit},
        )

        return [
            RetrievedDocument(
                id=row.id,
                title=row.title,
                source=row.source,
                content=row.content,
                url=row.url,
                similarity=row.semantic_sim,
                retrieval_method="recency",
                citation=self._format_citation(row.title, row.source, row.url),
            )
            for row in result.fetchall()
            if row.semantic_sim >= min_similarity * 0.9
        ]

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract important keywords from query."""
        # Remove common words
        stopwords = {
            "what", "is", "are", "the", "a", "an", "how", "does", "do", "can",
            "about", "in", "on", "for", "to", "of", "and", "or", "with"
        }
        words = query.lower().split()
        keywords = [w for w in words if w not in stopwords and len(w) > 2]
        return keywords[:5]  # Top 5 keywords

    def _rerank_results(
        self,
        documents: list[RetrievedDocument],
        query: str,
    ) -> list[RetrievedDocument]:
        """
        Re-rank results for better relevance.

        Simple heuristics:
        - Boost exact query term matches
        - Penalize very short documents
        - Prefer documents with complete sentences
        """
        query_terms = set(query.lower().split())

        for doc in documents:
            boost = 0.0

            # Exact term matching boost
            doc_terms = set(doc.content.lower().split())
            overlap = len(query_terms & doc_terms) / len(query_terms) if query_terms else 0
            boost += overlap * 0.1

            # Length penalty for very short docs
            if len(doc.content) < 100:
                boost -= 0.05

            # Boost for documents from authoritative sources
            if doc.source and doc.source.lower() in ["moneypuck", "evolving-hockey", "natural-stat-trick"]:
                boost += 0.05

            doc.similarity = min(1.0, doc.similarity + boost)

        # Re-sort by adjusted similarity
        return sorted(documents, key=lambda d: d.similarity, reverse=True)

    def _format_citation(
        self,
        title: str | None,
        source: str | None,
        url: str | None,
    ) -> str:
        """Format a citation string for the document."""
        parts = []
        if title:
            parts.append(f'"{title}"')
        if source:
            parts.append(f"({source})")
        if url:
            parts.append(f"[link]({url})")

        return " ".join(parts) if parts else "[Untitled document]"


# Singleton instance
rag_service = RAGService()


# -------------------------------------------------------------------------
# Document chunking utilities
# -------------------------------------------------------------------------


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[str]:
    """
    Split text into overlapping chunks for embedding.

    Args:
        text: Text to chunk
        chunk_size: Target size of each chunk in characters
        overlap: Number of characters to overlap between chunks
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Try to break at a sentence or paragraph boundary
        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + chunk_size // 2:
                end = para_break + 2
            else:
                # Look for sentence break
                for punct in [". ", "! ", "? "]:
                    sent_break = text.rfind(punct, start, end)
                    if sent_break > start + chunk_size // 2:
                        end = sent_break + 2
                        break

        chunks.append(text[start:end].strip())
        start = end - overlap

    return [c for c in chunks if c]  # Filter empty chunks
