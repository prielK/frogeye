"""Record/replay LLM backend: the eval harness's only way to a model.

`RecordingBackend` implements the app's `LLMBackend` protocol, so every call still goes
through `AccountedLLM` (ledger, cache, budget) exactly as in production. Each request is
keyed by a sha256 of everything that determines its answer: the task, model, reasoning
effort, output schema (as JSON schema), system and user text for a structured call; the
messages and tool definitions for a tool turn; the model, dimensions and text for each
embedded text (keyed per text, so a batch's make-up does not matter).

- replay (the default): answers only from the recordings. A miss raises `NotRecorded`
  (AccountedLLM reports it as an LLMError) and is logged on the running case, which the
  harness then reports as "not recorded" instead of scoring a degraded answer. It never
  calls a model.
- record: answers from the recordings when it can, otherwise calls the live backend and
  appends the request's answer to `recordings/<suite>.jsonl`.

Either way the caller gets the DECODED recording (embeddings round-trip through float16),
so a record run and its replay see byte-identical answers and score the same.

A replayed answer carries zero usage (nothing was spent); the cost it had when recorded is
tallied in `replayed_usd`.
"""

import base64
import contextvars
import dataclasses
import json
import logging
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.runtime.accounted import request_hash
from app.agents.runtime.backend import LLMBackend
from app.agents.runtime.openai_backend import _encoding
from app.agents.runtime.pricing import cost_usd
from app.agents.runtime.types import (
    BackendError,
    ChatMessage,
    EmbedOut,
    StructuredOut,
    ToolCall,
    ToolDef,
    ToolTurn,
    Usage,
)

logger = logging.getLogger(__name__)

Mode = Literal["record", "replay"]
CORPUS = "corpus"  # the recording file of the setup step (indexing the fixture corpus)


class NotRecorded(Exception):
    """A replay asked for a request the recordings do not hold."""


@dataclass
class CaseLog:
    """Requests one case asked for that the recordings did not hold."""

    misses: list[str] = field(default_factory=list)


_case: contextvars.ContextVar[CaseLog | None] = contextvars.ContextVar("eval_case", default=None)
_suite: contextvars.ContextVar[str] = contextvars.ContextVar("eval_suite", default=CORPUS)


@contextmanager
def case_scope() -> Iterator[CaseLog]:
    """Attribute the misses of the calls made inside to one case (per asyncio task)."""
    log = CaseLog()
    token = _case.set(log)
    try:
        yield log
    finally:
        _case.reset(token)


@contextmanager
def suite_scope(name: str) -> Iterator[None]:
    """New recordings made inside go to `recordings/<name>.jsonl`."""
    token = _suite.set(name)
    try:
        yield
    finally:
        _suite.reset(token)


# ---- keys and encodings -----------------------------------------------------------------


def structured_key(
    *, task: str, model: str, effort: str, schema: type[BaseModel], system: str, user: str
) -> str:
    return request_hash(
        "structured", task, model, effort, schema.__name__, schema.model_json_schema(), system, user
    )


def tool_turn_key(
    *,
    task: str,
    model: str,
    effort: str,
    system: str,
    messages: list[ChatMessage],
    tools: list[ToolDef],
) -> str:
    return request_hash(
        "tool_turn",
        task,
        model,
        effort,
        system,
        [dataclasses.asdict(m) for m in messages],
        [dataclasses.asdict(t) for t in tools],
    )


def embed_key(*, model: str, dims: int, text: str) -> str:
    return request_hash("embed", model, dims, text)


def encode_vector(vector: list[float]) -> str:
    """Little-endian float16, base64: 512 dims in about 1.4 kB (cosine ranks are unaffected
    at this precision, and record and replay both use the decoded value)."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def decode_vector(data: str) -> list[float]:
    raw = base64.b64decode(data)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def _entry(
    key: str, kind: str, task: str, model: str, usage: Usage, **answer: Any
) -> dict[str, Any]:
    """One recording line: the key, what it was (for a reader), its usage and its answer."""
    return {
        "key": key,
        "kind": kind,
        "task": task,
        "model": model,
        "usage": [usage.input_tokens, usage.cached_tokens, usage.output_tokens],
        **answer,
    }


# ---- the backend ------------------------------------------------------------------------


class RecordingBackend:
    """`LLMBackend` over the recordings in `directory` (see the module docstring)."""

    def __init__(
        self,
        directory: Path,
        mode: Mode,
        live: LLMBackend | None = None,
        *,
        budget_usd: float | None = None,
    ) -> None:
        if mode == "record" and live is None:
            raise ValueError("record mode needs a live backend")
        self.directory = directory
        self.mode = mode
        self._live = live
        self.budget_usd = budget_usd  # live spend after which no live call starts
        self._entries: dict[str, dict[str, Any]] = {}
        self._file_of: dict[str, str] = {}
        self.used: set[str] = set()
        self.live_calls = 0
        self.replayed_calls = 0
        self.live_usd = 0.0
        self.replayed_usd = 0.0
        self.misses = 0
        for path in sorted(directory.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._entries.setdefault(entry["key"], entry)
                    self._file_of.setdefault(entry["key"], path.stem)

    # -- bookkeeping

    def _lookup(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is not None:
            self.used.add(key)
        return entry

    def _replayed(self, entry: dict[str, Any]) -> None:
        self.replayed_calls += 1
        self.replayed_usd += cost_usd(entry["model"], *entry["usage"])

    def _miss(self, what: str) -> NotRecorded:
        self.misses += 1
        if (log := _case.get()) is not None:
            log.misses.append(what)
        return NotRecorded(f"not recorded: {what}")

    def _go_live(self, what: str) -> LLMBackend:
        """The live backend for a request the recordings lack; raises NotRecorded in a
        replay, or once the live spend reaches the budget (the case is then not scored)."""
        if self.mode == "replay" or self._live is None:
            raise self._miss(what)
        if self.budget_usd is not None and self.live_usd >= self.budget_usd:
            raise self._miss(f"{what}; budget of ${self.budget_usd:.2f} reached")
        return self._live

    def _append(self, entry: dict[str, Any]) -> None:
        suite = _suite.get()
        self._entries[entry["key"]] = entry
        self._file_of[entry["key"]] = suite
        self.used.add(entry["key"])
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f"{suite}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _spent(self, model: str, usage: Usage) -> None:
        self.live_calls += 1
        self.live_usd += cost_usd(
            model, usage.input_tokens, usage.cached_tokens, usage.output_tokens
        )

    def prune(self, files: set[str]) -> int:
        """Rewrite the recording files named in `files` (after a full record run) with only
        the entries this run used; other files are left as they are. An entry belongs to
        one file (the first that holds it), so a duplicate elsewhere is dropped too.
        Returns how many unused entries were dropped."""
        kept: dict[str, list[dict[str, Any]]] = {name: [] for name in files}
        dropped = 0
        for key, entry in list(self._entries.items()):
            home = self._file_of[key]
            if home not in files:
                continue
            if key in self.used:
                kept[home].append(entry)
            else:
                del self._entries[key]
                dropped += 1
        for name, entries in kept.items():
            path = self.directory / f"{name}.jsonl"
            if not entries:
                path.unlink(missing_ok=True)
                continue
            lines = sorted(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries)
            text = "".join(line + "\n" for line in lines)
            path.write_text(text, encoding="utf-8", newline="\n")
        return dropped

    def adopt(self, keys: set[str], name: str) -> int:
        """Give `name`'s file its own copy of these recordings (an experiment keeps the
        answers it compared, whatever a later prune of the suites' files drops)."""
        moved = 0
        for key in sorted(keys):
            entry = self._entries.get(key)
            if entry is None or self._file_of[key] == name:
                continue
            self._file_of[key] = name
            with (self.directory / f"{name}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            moved += 1
        return moved

    # -- LLMBackend

    async def structured(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        schema: type[BaseModel],
        system: str,
        user: str,
    ) -> StructuredOut:
        key = structured_key(
            task=task, model=model, effort=effort, schema=schema, system=system, user=user
        )
        entry = self._lookup(key)
        if entry is not None:
            self._replayed(entry)
            return _structured_answer(entry, schema, Usage())
        live = self._go_live(f"{task} (structured)")
        try:
            out = await live.structured(
                task=task, model=model, effort=effort, schema=schema, system=system, user=user
            )
        except BackendError as exc:  # unusable model output: replayed as the same failure
            self._spent(model, exc.usage)
            self._append(_entry(key, "structured", task, model, exc.usage, error=str(exc)))
            raise
        self._spent(model, out.usage)
        if out.refusal is not None:
            answer: dict[str, Any] = {"refusal": out.refusal}
        else:
            value = schema.model_validate(out.value)
            answer = {"value": value.model_dump(mode="json")}
        entry = _entry(key, "structured", task, model, out.usage, **answer)
        self._append(entry)
        return _structured_answer(entry, schema, out.usage)

    async def tool_turn(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
    ) -> ToolTurn:
        key = tool_turn_key(
            task=task, model=model, effort=effort, system=system, messages=messages, tools=tools
        )
        entry = self._lookup(key)
        if entry is not None:
            self._replayed(entry)
            return _turn_answer(entry, Usage())
        live = self._go_live(f"{task} (tool turn {len(messages)})")
        turn = await live.tool_turn(
            task=task, model=model, effort=effort, system=system, messages=messages, tools=tools
        )
        self._spent(model, turn.usage)
        entry = _entry(
            key,
            "tool_turn",
            task,
            model,
            turn.usage,
            content=turn.content,
            tool_calls=[dataclasses.asdict(tc) for tc in turn.tool_calls],
        )
        self._append(entry)
        return _turn_answer(entry, turn.usage)

    async def embed(self, *, task: str, model: str, dims: int, texts: list[str]) -> EmbedOut:
        keys = [embed_key(model=model, dims=dims, text=t) for t in texts]
        found = {k: e for k in keys if (e := self._lookup(k)) is not None}
        missing = [(k, t) for k, t in zip(keys, texts, strict=True) if k not in found]
        live = self._go_live(f"{task} (embedding of {len(missing)} texts)") if missing else None
        for entry in found.values():
            self._replayed(entry)
        usage = Usage()
        if live is not None:
            out = await live.embed(task=task, model=model, dims=dims, texts=[t for _, t in missing])
            usage = out.usage
            self._spent(model, out.usage)
            enc = _encoding()  # the live backend's own token estimate, split per text
            for (key, text), vector in zip(missing, out.vectors, strict=True):
                tokens = Usage(input_tokens=len(enc.encode(text, disallowed_special=())))
                entry = _entry(key, "embed", task, model, tokens, vector=encode_vector(vector))
                self._append(entry)
                found[key] = entry
        vectors = [decode_vector(found[k]["vector"]) for k in keys]
        return EmbedOut(vectors=vectors, usage=usage, model=model)


def _structured_answer(
    entry: dict[str, Any], schema: type[BaseModel], usage: Usage
) -> StructuredOut:
    if "error" in entry:
        raise BackendError(entry["error"], usage=usage)
    if "refusal" in entry:
        refusal = entry["refusal"]
        return StructuredOut(value=None, usage=usage, model=entry["model"], refusal=refusal)
    return StructuredOut(
        value=schema.model_validate(entry["value"]), usage=usage, model=entry["model"]
    )


def _turn_answer(entry: dict[str, Any], usage: Usage) -> ToolTurn:
    calls = [ToolCall(id=c["id"], name=c["name"], args=c["args"]) for c in entry["tool_calls"]]
    return ToolTurn(content=entry["content"], tool_calls=calls, usage=usage, model=entry["model"])
