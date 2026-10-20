from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk

from docqa.rag import HUMAN_PROMPT, SYSTEM_PROMPT, _chunk_text, format_context, get_llm, get_prompt


def test_context_lists_page_numbers():
    ctx = format_context([Document(page_content="alpha", metadata={"page": 3}), Document(page_content="beta", metadata={"page": 7})])
    assert "[Excerpt 1, p. 3]\nalpha" in ctx
    assert "[Excerpt 2, p. 7]\nbeta" in ctx


def test_prompt_renders_both_roles():
    msgs = get_prompt().format_messages(context="CTX", question="Q?")
    assert msgs[0].content == SYSTEM_PROMPT
    assert msgs[1].content == HUMAN_PROMPT.format(context="CTX", question="Q?")
    assert "[p. N]" in SYSTEM_PROMPT


def test_chunk_text_handles_string_and_block_content():
    assert _chunk_text(AIMessageChunk(content="hi")) == "hi"
    assert _chunk_text(AIMessageChunk(content=[{"type": "text", "text": "a"}, {"type": "thinking", "thinking": "x"}, {"type": "text", "text": "b"}])) == "ab"


def test_llm_is_configured_from_settings():
    llm = get_llm()
    assert llm.model.endswith("gemini-3.6-flash")
    assert llm.max_output_tokens == 4096
    assert llm.max_retries == 4
