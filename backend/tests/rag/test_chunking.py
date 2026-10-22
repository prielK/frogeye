import tiktoken

from app.rag.chunking import chunk_text

ENC = tiktoken.get_encoding("o200k_base")


def _para(i: int) -> str:
    return " ".join(f"Sentence {i}-{j} talks about artifact registries." for j in range(12))


def test_chunk_sizes_and_overlap():
    text = "\n\n".join(_para(i) for i in range(30))
    chunks = chunk_text(text)
    assert len(chunks) > 3
    assert all(len(ENC.encode(c)) <= 600 for c in chunks)
    # consecutive chunks share a tail/head overlap
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt[:40] in prev
    assert text.startswith(chunks[0][:50])
    assert text.rstrip().endswith(chunks[-1].rstrip()[-50:])
    # every sentence is covered by some chunk
    joined = "\n".join(chunks)
    assert all(f"Sentence {i}-{j} " in joined for i in range(30) for j in range(12))


def test_chunk_splits_oversized_sentence():
    text = "word " * 3000  # one unbroken "sentence"
    chunks = chunk_text(text, max_tokens=100, overlap=10)
    assert all(len(ENC.encode(c)) <= 100 for c in chunks)
    assert "".join(chunks).split() == text.split()


def test_chunk_short_text_is_single_chunk():
    assert chunk_text("Hello world.") == ["Hello world."]


def test_chunk_blank():
    assert chunk_text("") == []
    assert chunk_text("  \n\n \t") == []
