"""API tests with the database and model calls replaced by fakes."""

import io
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from docqa import main as api
from docqa.models import Document, DocumentStatus
from docqa.rag import AnswerEvent


class FakeSession:
    def __init__(self, docs=None):
        self.docs = {d.id: d for d in (docs or [])}
        self.added = []
        self.committed = False

    async def get(self, model, key):
        return self.docs.get(key)

    async def execute(self, stmt):
        class R:
            def scalar_one_or_none(self_inner):
                return None

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return list(self.docs.values())

        return R()

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass

    async def delete(self, obj):
        self.docs.pop(obj.id, None)

    async def refresh(self, obj):
        now = datetime.now(UTC)
        obj.created_at = obj.created_at or now
        obj.updated_at = obj.updated_at or now


def _doc(status=DocumentStatus.ready) -> Document:
    now = datetime.now(UTC)
    return Document(id=uuid.uuid4(), filename="a.pdf", sha256="x" * 64, size_bytes=10, content=b"%PDF-", status=status, page_count=2, chunk_count=5, error=None, created_at=now, updated_at=now)


AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch):
    ready = _doc()
    queued = _doc(DocumentStatus.queued)
    session = FakeSession([ready, queued])

    async def fake_session():
        yield session

    api.app.dependency_overrides[api.get_session] = fake_session
    monkeypatch.setattr(api, "get_embeddings", lambda: object())

    async def fake_answer(session, embeddings, document_id, question):
        yield AnswerEvent(type="sources", sources=[{"page": 2, "score": 0.9, "excerpt": "e"}])
        yield AnswerEvent(type="delta", text="Answer [p. 2]")
        yield AnswerEvent(type="done", usage={"input_tokens": 10, "output_tokens": 3})

    monkeypatch.setattr(api, "answer", fake_answer)
    api.app.router.lifespan_context = _noop_lifespan
    yield AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test", headers=AUTH), ready, queued, session
    api.app.dependency_overrides.clear()


from contextlib import asynccontextmanager


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.mark.anyio
async def test_health(client):
    c, *_ = client
    assert (await c.get("/healthz")).text == "ok"


@pytest.mark.anyio
async def test_upload_rejects_non_pdf(client):
    c, *_ = client
    r = await c.post("/api/documents", files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")})
    assert r.status_code == 415


@pytest.mark.anyio
async def test_upload_queues_pdf(client):
    c, _, _, session = client
    r = await c.post("/api/documents", files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.7 fake"), "application/pdf")})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    assert session.committed and len(session.added) == 2  # document + job


@pytest.mark.anyio
async def test_ask_requires_ready_document(client):
    c, _, queued, _ = client
    r = await c.post(f"/api/documents/{queued.id}/ask", json={"question": "what?"})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_ask_streams_events(client):
    c, ready, *_ = client
    r = await c.post(f"/api/documents/{ready.id}/ask", json={"question": "what?"})
    assert r.status_code == 200
    body = r.text
    assert "event: sources" in body and "event: delta" in body and "event: done" in body
    assert "Answer [p. 2]" in body


@pytest.mark.anyio
async def test_bearer_token_enforced(client):
    c, *_ = client
    assert (await c.get("/api/documents", headers={"Authorization": ""})).status_code == 401
    assert (await c.get("/api/documents", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await c.get("/api/documents")).status_code == 200
    assert (await c.get("/healthz", headers={"Authorization": ""})).status_code == 200


def test_empty_api_token_is_rejected():
    import pytest as _pytest
    from docqa.config import Settings

    with _pytest.raises(ValueError):
        Settings(api_token="   ")  # type: ignore[call-arg]


def test_database_url_is_secret_and_normalised():
    from docqa.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert "docqa:docqa@" not in repr(s) and "docqa:docqa@" not in str(s)
    assert s.database_url.get_secret_value().startswith("postgresql+psycopg://")
