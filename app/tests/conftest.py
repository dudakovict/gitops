import os

import pytest

# Unit tests never talk to real services. Keys are placeholders so Settings validates.
os.environ.setdefault("DATABASE_URL", "postgresql://docqa:docqa@localhost:5432/docqa")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("DOCQA_API_TOKEN", "test-token")


@pytest.fixture
def anyio_backend():
    return "asyncio"
