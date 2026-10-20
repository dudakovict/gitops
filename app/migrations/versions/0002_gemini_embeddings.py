"""switch embeddings to gemini-embedding-2 at 1536 dimensions

Existing vectors were produced by a different model and are not comparable, so the chunks
are dropped and every ready document is re-queued for ingestion.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("TRUNCATE chunks")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536)")
    op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("UPDATE documents SET status = 'queued', chunk_count = NULL, error = NULL WHERE status = 'ready'")
    op.execute(
        "INSERT INTO jobs (id, document_id, status) "
        "SELECT gen_random_uuid(), d.id, 'queued' FROM documents d "
        "WHERE d.status = 'queued' AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.document_id = d.id AND j.status IN ('queued', 'running'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("TRUNCATE chunks")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)")
    op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
