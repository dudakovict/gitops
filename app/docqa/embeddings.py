"""Embedding model wiring. One place to swap Gemini for another provider or a local model."""

from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from .config import get_settings


@lru_cache
def get_embeddings() -> Embeddings:
    settings = get_settings()
    # task_type is left unset: the integration sends RETRIEVAL_DOCUMENT for embed_documents
    # and RETRIEVAL_QUERY for embed_query, which is what asymmetric RAG retrieval wants.
    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        google_api_key=settings.google_api_key,
        output_dimensionality=settings.embedding_dim,
    )
