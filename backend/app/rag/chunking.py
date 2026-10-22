"""Token-aware text chunking (tiktoken o200k_base): paragraph, then sentence, boundaries."""

import re
from functools import lru_cache

import tiktoken

ENCODING = "o200k_base"
_PARAGRAPH = re.compile(r".*?(?:\n[ \t]*\n\s*|\Z)", re.S)
_SENTENCE = re.compile(r".*?(?:[.!?]+[\"')\]]*\s+|\Z)", re.S)


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(ENCODING)


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _pieces(pattern: re.Pattern[str], text: str) -> list[str]:
    return [p for p in pattern.findall(text) if p]


def _split_tokens(text: str, max_tokens: int) -> list[str]:
    enc = _encoding()
    tokens = enc.encode(text)
    return [enc.decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens)]


def _units(text: str, max_tokens: int) -> list[tuple[str, int, bool]]:
    """Sentence-level pieces `(text, tokens, ends_paragraph)` of at most `max_tokens` tokens
    whose concatenation is `text`."""
    units: list[tuple[str, int, bool]] = []
    for para in _pieces(_PARAGRAPH, text):
        start = len(units)
        for sentence in _pieces(_SENTENCE, para):
            n = count_tokens(sentence)
            if n <= max_tokens:
                units.append((sentence, n, False))
            else:
                units.extend(
                    (s, count_tokens(s), False) for s in _split_tokens(sentence, max_tokens)
                )
        if len(units) > start:
            last = units[-1]
            units[-1] = (last[0], last[1], True)
    return units


def chunk_text(text: str, *, max_tokens: int = 600, overlap: int = 80) -> list[str]:
    """Chunks of at most `max_tokens` tokens. Chunks end at a paragraph boundary when one
    falls in the second half of the window, otherwise at a sentence boundary; each chunk
    starts with up to `overlap` tokens (whole sentences) of the previous chunk's tail.
    Blank text gives []."""
    if not text or not text.strip():
        return []
    if max_tokens < 1 or not 0 <= overlap < max_tokens:
        raise ValueError("need max_tokens >= 1 and 0 <= overlap < max_tokens")
    chunks: list[str] = []
    current: list[tuple[str, int, bool]] = []
    carried = 0  # leading units of `current` that are overlap from the previous chunk
    size = 0

    def flush(incoming: int) -> None:
        nonlocal current, carried, size
        cut, running = len(current), 0
        for idx, (_, n, ends) in enumerate(current):
            running += n
            if ends and running >= max_tokens / 2 and idx + 1 < len(current):
                cut = idx + 1  # latest paragraph boundary is preferred, so keep scanning
        flushed, rest = current[:cut], current[cut:]
        chunks.append("".join(u for u, _, _ in flushed))
        tail: list[tuple[str, int, bool]] = []
        tail_size = 0
        for u in reversed(flushed):
            if tail_size + u[1] > overlap:
                break
            tail.insert(0, u)
            tail_size += u[1]
        rest_size = sum(n for _, n, _ in rest)
        while tail and tail_size + rest_size + incoming > max_tokens:
            tail_size -= tail.pop(0)[1]
        current, carried, size = tail + rest, len(tail), tail_size + rest_size

    for unit in _units(text, max_tokens):
        while current and size + unit[1] > max_tokens:
            if len(current) == carried:  # only overlap left: nothing new to emit
                current, carried, size = [], 0, 0
            else:
                flush(unit[1])
        current.append(unit)
        size += unit[1]
    if len(current) > carried:
        chunks.append("".join(u for u, _, _ in current))
    return [c for c in chunks if c.strip()]
