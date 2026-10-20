"""Retrieval and answer generation.

LangChain pieces: a BaseRetriever over our pgvector table, a ChatPromptTemplate, and
ChatGoogleGenerativeAI streaming. The retriever is deliberately our own so page numbers and
document scoping stay first-class instead of living in a generic metadata JSON blob.
"""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessageChunk
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import Chunk
from .telemetry import answer_latency, llm_input_tokens, llm_output_tokens, questions_asked, tracer

SYSTEM_PROMPT = """You answer questions about one PDF document using only the excerpts provided.

Rules:
- Base every statement on the excerpts. If they do not contain the answer, say so plainly.
- Cite pages inline as [p. N] right after the claim they support. Use the page numbers given with each excerpt.
- Quote exact figures, names and dates as written in the excerpts.
- Be concise. No preamble about what you are going to do."""

HUMAN_PROMPT = """Excerpts from the document:

{context}

Question: {question}"""


def format_context(docs: list[LCDocument]) -> str:
    parts = []
    for i, d in enumerate(docs, start=1):
        parts.append(f"[Excerpt {i}, p. {d.metadata['page']}]\n{d.page_content}")
    return "\n\n".join(parts)


@lru_cache
def get_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)])


class PgVectorRetriever(BaseRetriever):
    """Top-k cosine neighbours within one document."""

    session: AsyncSession
    embeddings: Embeddings
    document_id: uuid.UUID
    top_k: int

    model_config = {"arbitrary_types_allowed": True}

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[LCDocument]:
        raise NotImplementedError("use the async interface")

    async def _aget_relevant_documents(self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun) -> list[LCDocument]:
        with tracer.start_as_current_span("rag.retrieve") as span:
            span.set_attribute("docqa.document.id", str(self.document_id))
            span.set_attribute("docqa.top_k", self.top_k)
            vector = await self.embeddings.aembed_query(query)
            distance = Chunk.embedding.cosine_distance(vector).label("distance")
            stmt = (
                select(Chunk, distance)
                .where(Chunk.document_id == self.document_id)
                .order_by(distance)
                .limit(self.top_k)
            )
            rows = (await self.session.execute(stmt)).all()
            docs = [
                LCDocument(
                    page_content=chunk.text,
                    metadata={"page": chunk.page, "ordinal": chunk.ordinal, "score": 1.0 - float(dist)},
                )
                for chunk, dist in rows
            ]
            # Present excerpts in reading order; the model does not need them ranked.
            docs.sort(key=lambda d: (d.metadata["page"], d.metadata["ordinal"]))
            span.set_attribute("docqa.retrieved", len(docs))
            return docs


@lru_cache
def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
        max_output_tokens=settings.llm_max_tokens,
        max_retries=4,   # free-tier capacity errors (503) are transient
    )


@dataclass
class AnswerEvent:
    type: str  # "sources" | "delta" | "done"
    text: str = ""
    sources: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def _chunk_text(chunk: AIMessageChunk) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            out.append(block.get("text", ""))
        elif isinstance(block, str):
            out.append(block)
    return "".join(out)


async def answer(session: AsyncSession, embeddings: Embeddings, document_id: uuid.UUID, question: str) -> AsyncIterator[AnswerEvent]:
    """Stream an answer. First event lists the sources, then text deltas, then usage."""
    settings = get_settings()
    retriever = PgVectorRetriever(session=session, embeddings=embeddings, document_id=document_id, top_k=settings.top_k)
    questions_asked.add(1)
    started = time.perf_counter()
    first_token_at: float | None = None

    docs = await retriever.ainvoke(question)
    sources = [{"page": d.metadata["page"], "score": round(d.metadata["score"], 4), "excerpt": d.page_content[:300]} for d in docs]
    yield AnswerEvent(type="sources", sources=sources)

    chain = get_prompt() | get_llm()
    usage: dict = {}
    with tracer.start_as_current_span("llm.generate") as span:
        span.set_attribute("gen_ai.request.model", settings.llm_model)
        span.set_attribute("docqa.document.id", str(document_id))
        async for chunk in chain.astream({"context": format_context(docs), "question": question}):
            text = _chunk_text(chunk)
            if text:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                    answer_latency.record(first_token_at - started)
                yield AnswerEvent(type="delta", text=text)
            if chunk.usage_metadata:
                usage = dict(chunk.usage_metadata)
        if usage:
            span.set_attribute("gen_ai.usage.input_tokens", usage.get("input_tokens", 0))
            span.set_attribute("gen_ai.usage.output_tokens", usage.get("output_tokens", 0))
            llm_input_tokens.add(usage.get("input_tokens", 0))
            llm_output_tokens.add(usage.get("output_tokens", 0))
    yield AnswerEvent(type="done", usage=usage)
