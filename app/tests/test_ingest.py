import pymupdf

from docqa.ingest import PageText, chunk_pages, extract_pages


def _pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    return doc.tobytes()


def test_extract_pages_keeps_page_numbers():
    pages = extract_pages(_pdf(["first page", "second page", ""]))
    assert [p.page for p in pages] == [1, 2, 3]
    assert pages[0].text == "first page"
    assert pages[2].text == ""


def test_chunks_never_cross_pages_and_skip_empty_pages():
    long = "word " * 1000
    chunks = chunk_pages([PageText(1, long), PageText(2, ""), PageText(3, "short")], chunk_size=500, chunk_overlap=50)
    assert all(c.page in (1, 3) for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert sum(1 for c in chunks if c.page == 1) > 5
    assert chunks[-1].page == 3 and chunks[-1].text == "short"
    assert all(len(c.text) <= 500 for c in chunks)
