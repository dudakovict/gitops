"""Ingestion worker: claims queued jobs from Postgres and runs the pipeline.

Claiming uses FOR UPDATE SKIP LOCKED so several workers never pick the same job.
Jobs that stop heart-beating (worker died mid-run) are re-queued by the reaper.
"""

import asyncio
import logging
import signal
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from . import db, telemetry
from .config import get_settings
from .embeddings import get_embeddings
from .ingest import ingest_document
from .logging import configure_logging
from .models import Document, DocumentStatus, Job, JobStatus
from .telemetry import documents_failed, documents_ingested, ingest_latency, tracer

log = logging.getLogger("docqa.worker")
WORKER_ID = f"{socket.gethostname()}"


async def claim_job() -> Job | None:
    async with db.session() as session:
        candidate = (
            select(Job.id)
            .where(Job.status == JobStatus.queued)
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = (
            update(Job)
            .where(Job.id == candidate)
            .values(status=JobStatus.running, worker=WORKER_ID, attempts=Job.attempts + 1, started_at=func.now(), heartbeat_at=func.now())
            .returning(Job)
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is not None:
            await session.execute(update(Document).where(Document.id == job.document_id).values(status=DocumentStatus.processing, error=None))
        await session.commit()
        return job


async def reap_stale_jobs(stale_minutes: int, max_attempts: int) -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
    async with db.session() as session:
        stale = (await session.execute(select(Job).where(Job.status == JobStatus.running, Job.heartbeat_at < cutoff))).scalars().all()
        for job in stale:
            if job.attempts >= max_attempts:
                job.status = JobStatus.failed
                job.error = "worker stopped responding and attempts are exhausted"
                job.finished_at = datetime.now(UTC)
                await session.execute(update(Document).where(Document.id == job.document_id).values(status=DocumentStatus.failed, error=job.error))
            else:
                job.status = JobStatus.queued
                job.worker = None
            log.warning("re-queued stale job", extra={"ctx_job_id": str(job.id), "ctx_attempts": job.attempts})
        await session.commit()


async def process(job: Job) -> None:
    settings = get_settings()
    started = asyncio.get_running_loop().time()
    # Plain values only from here on. After a rollback the ORM expires its instances, and
    # touching an expired attribute in async code raises MissingGreenlet.
    job_id, document_id, attempts = job.id, job.document_id, job.attempts
    async with db.session() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            await session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.failed, error="document vanished", finished_at=func.now()))
            await session.commit()
            return
        pdf_bytes = doc.content

        async def heartbeat() -> None:
            await session.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=func.now()))
            await session.commit()

        with tracer.start_as_current_span("worker.job") as span:
            span.set_attribute("docqa.job.id", str(job_id))
            span.set_attribute("docqa.document.id", str(document_id))
            try:
                pages, chunks = await ingest_document(session, document_id, pdf_bytes, get_embeddings(), heartbeat)
                await session.execute(
                    update(Document).where(Document.id == document_id).values(status=DocumentStatus.ready, page_count=pages, chunk_count=chunks, error=None)
                )
                await session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.done, finished_at=func.now()))
                await session.commit()
                documents_ingested.add(1)
                ingest_latency.record(asyncio.get_running_loop().time() - started)
                log.info("document ready", extra={"ctx_document_id": str(document_id), "ctx_pages": pages, "ctx_chunks": chunks})
            except Exception as exc:
                await session.rollback()
                span.record_exception(exc)
                retry = attempts < settings.job_max_attempts
                err = f"{type(exc).__name__}: {exc}"[:2000]
                await session.execute(
                    update(Job).where(Job.id == job_id).values(status=JobStatus.queued if retry else JobStatus.failed, error=err, finished_at=None if retry else func.now())
                )
                await session.execute(
                    update(Document).where(Document.id == document_id).values(status=DocumentStatus.queued if retry else DocumentStatus.failed, error=err)
                )
                await session.commit()
                if not retry:
                    documents_failed.add(1)
                log.exception("ingestion failed", extra={"ctx_document_id": str(document_id), "ctx_attempt": attempts, "ctx_retry": retry})


async def main() -> None:
    configure_logging()
    telemetry.configure("docqa-worker")
    settings = get_settings()
    get_embeddings()  # fail fast on a missing key
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started", extra={"ctx_worker": WORKER_ID, "ctx_env": settings.env})

    last_reap = 0.0
    while not stop.is_set():
        try:
            if loop.time() - last_reap > 60:
                await reap_stale_jobs(settings.job_stale_minutes, settings.job_max_attempts)
                last_reap = loop.time()
            job = await claim_job()
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            await process(job)
        except OperationalError:
            log.exception("database unavailable, backing off")
            await asyncio.sleep(5)
        except Exception:
            # One bad job must never take the worker down; the reaper re-queues it later.
            log.exception("unexpected error in worker loop")
            await asyncio.sleep(1)
    await db.dispose()
    log.info("worker stopped")


def run() -> None:
    asyncio.run(main())
