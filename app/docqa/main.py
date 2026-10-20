"""HTTP API and the single-page UI."""

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from . import db, telemetry
from .auth import require_token
from .config import Settings, get_settings
from .embeddings import get_embeddings
from .logging import configure_logging
from .models import Document, DocumentStatus, Job
from .rag import answer
from .schemas import AskIn, DocumentList, DocumentOut

log = logging.getLogger("docqa.api")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    telemetry.configure("docqa-api")
    SQLAlchemyInstrumentor().instrument(engine=db.engine().sync_engine)
    get_embeddings()  # fail fast on a missing key
    log.info("api started", extra={"ctx_env": get_settings().env})
    yield
    await db.dispose()


app = FastAPI(title="docqa", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,static")


async def get_session():
    async with db.session() as s:
        yield s


# ---------------------------------------------------------------- health
@app.get("/healthz", include_in_schema=False)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/readyz", include_in_schema=False)
async def readyz(session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    await session.execute(text("SELECT 1"))
    return PlainTextResponse("ready")


# ---------------------------------------------------------------- documents
@app.post("/api/documents", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
async def upload_document(file: UploadFile, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> Document:
    head = await file.read(5)
    if head != b"%PDF-":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF files are accepted")
    hasher = hashlib.sha256(head)
    buf = bytearray(head)
    while True:
        part = await file.read(1024 * 1024)
        if not part:
            break
        buf.extend(part)
        hasher.update(part)
        if len(buf) > settings.max_upload_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"file exceeds {settings.max_upload_mb} MB")
    content = bytes(buf)
    sha = hasher.hexdigest()

    existing = (await session.execute(select(Document).where(Document.sha256 == sha))).scalar_one_or_none()
    if existing is not None:
        return existing  # idempotent re-upload

    doc = Document(filename=(file.filename or "upload.pdf")[:512], sha256=sha, size_bytes=len(content), content=content, status=DocumentStatus.queued)
    session.add(doc)
    await session.flush()
    session.add(Job(document_id=doc.id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(select(Document).where(Document.sha256 == sha))).scalar_one()
        return existing
    await session.refresh(doc)  # load server-generated timestamps and defaults
    log.info("document queued", extra={"ctx_document_id": str(doc.id), "ctx_size": len(content)})
    return doc


@app.get("/api/documents", response_model=DocumentList, dependencies=[Depends(require_token)])
async def list_documents(session: AsyncSession = Depends(get_session)) -> DocumentList:
    rows = (await session.execute(select(Document).order_by(Document.created_at.desc()).limit(200))).scalars().all()
    return DocumentList(items=[DocumentOut.model_validate(r) for r in rows])


@app.get("/api/documents/{document_id}", response_model=DocumentOut, dependencies=[Depends(require_token)])
async def get_document(document_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return doc


@app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_token)])
async def delete_document(document_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> None:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    await session.delete(doc)
    await session.commit()


# ---------------------------------------------------------------- ask
@app.post("/api/documents/{document_id}/ask", dependencies=[Depends(require_token)])
async def ask(document_id: uuid.UUID, body: AskIn, request: Request, session: AsyncSession = Depends(get_session)):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if doc.status != DocumentStatus.ready:
        raise HTTPException(status.HTTP_409_CONFLICT, f"document is {doc.status.value}, not ready")

    async def events():
        try:
            async for ev in answer(session, get_embeddings(), document_id, body.question):
                if await request.is_disconnected():
                    break
                payload = {"text": ev.text} if ev.type == "delta" else {"sources": ev.sources} if ev.type == "sources" else {"usage": ev.usage}
                yield {"event": ev.type, "data": json.dumps(payload)}
        except Exception:
            log.exception("answer failed", extra={"ctx_document_id": str(document_id)})
            yield {"event": "error", "data": json.dumps({"detail": "answer generation failed"})}

    return EventSourceResponse(events())


# ---------------------------------------------------------------- UI
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def run() -> None:
    import uvicorn

    uvicorn.run("docqa.main:app", host="0.0.0.0", port=8080, log_config=None, proxy_headers=True, forwarded_allow_ips="*")
