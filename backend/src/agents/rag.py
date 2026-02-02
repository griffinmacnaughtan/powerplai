"""
RAG (Retrieval Augmented Generation) service for semantic search over hockey content.
"""
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import numpy as np

logger = structlog.get_logger()

# Using a small, fast model that runs well locally
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


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

    async def search(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.3,
    ) -> list[dict]:
        """
        Search for documents similar to the query.

        Uses cosine similarity via pgvector.
        """
        query_embedding = self.embed(query)

        result = await db.execute(
            text("""
                SELECT
                    id,
                    title,
                    source,
                    content,
                    url,
                    1 - (embedding <=> :embedding) as similarity
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :embedding
                LIMIT :limit
            """),
            {
                "embedding": str(query_embedding),
                "limit": limit,
            },
        )

        rows = result.fetchall()
        documents = []
        for row in rows:
            if row.similarity >= min_similarity:
                documents.append({
                    "id": row.id,
                    "title": row.title,
                    "source": row.source,
                    "content": row.content,
                    "url": row.url,
                    "similarity": round(row.similarity, 3),
                })

        logger.info("rag_search", query=query[:50], results=len(documents))
        return documents


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
