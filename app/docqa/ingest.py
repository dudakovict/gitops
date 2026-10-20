"""PDF -> page texts -> chunks with page numbers -> embeddings -> pgvector rows."""

import time
import uuid
from dataclasses import dataclass

import pymupdf
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import Chunk
from .telemetry import chunks_embedded, tracer


@dataclass(frozen=True)
class PageText:
    page: int  # 1-based
    text: str


@dataclass(frozen=True)
class ChunkText:
    ordinal: int
    page: int
    text: str


def extract_pages(pdf_bytes: bytes) -> list[PageText]:
    """Text per page. Pages without a text layer (scans) come back empty and are skipped later."""
    pages: list[PageText] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text")
            pages.append(PageText(page=index, text=text.strip()))
    return pages


def chunk_pages(pages: list[PageText], chunk_size: int, chunk_overlap: int) -> list[ChunkText]:
    """Split each page separately so every chunk carries an exact page number for citations."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks: list[ChunkText] = []
    ordinal = 0
    for page in pages:
        if not page.text:
            continue
        for piece in splitter.split_text(page.text):
            chunks.append(ChunkText(ordinal=ordinal, page=page.page, text=piece))
            ordinal += 1
    return chunks


async def embed_and_store(
    session: AsyncSession,
    document_id: uuid.UUID,
    chunks: list[ChunkText],
    embeddings: Embeddings,
    batch_size: int,
    heartbeat=None,
) -> int:
    """Embed in batches and insert; re-runnable because it clears the document's chunks first."""
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        with tracer.start_as_current_span("ingest.embed_batch") as span:
            span.set_attribute("docqa.batch.size", len(batch))
            vectors = await embeddings.aembed_documents([c.text for c in batch])
            if vectors and len(vectors[0]) != get_settings().embedding_dim:
                raise ValueError(f"embedding width {len(vectors[0])} does not match the schema ({get_settings().embedding_dim})")
        await session.execute(
            insert(Chunk),
            [
                {"document_id": document_id, "ordinal": c.ordinal, "page": c.page, "text": c.text, "embedding": v}
                for c, v in zip(batch, vectors, strict=True)
            ],
        )
        total += len(batch)
        chunks_embedded.add(len(batch))
        if heartbeat is not None:
            await heartbeat()
    return total


async def ingest_document(session: AsyncSession, document_id: uuid.UUID, pdf_bytes: bytes, embeddings: Embeddings, heartbeat=None) -> tuple[int, int]:
    """Returns (page_count, chunk_count). Caller owns the transaction and status updates."""
    settings = get_settings()
    started = time.perf_counter()
    with tracer.start_as_current_span("ingest.document") as span:
        span.set_attribute("docqa.document.id", str(document_id))
        pages = extract_pages(pdf_bytes)
        chunks = chunk_pages(pages, settings.chunk_size, settings.chunk_overlap)
        span.set_attribute("docqa.document.pages", len(pages))
        span.set_attribute("docqa.document.chunks", len(chunks))
        if not chunks:
            raise ValueError("PDF has no extractable text (scanned document without OCR?)")
        stored = await embed_and_store(session, document_id, chunks, embeddings, settings.embedding_batch_size, heartbeat)
        span.set_attribute("docqa.ingest.seconds", time.perf_counter() - started)
    return len(pages), stored
