"""Versioned prompt files and the untrusted-content delimiter.

A prompt file is `app/agents/prompts/{name}.md`: YAML front matter carrying `version`,
then a `## system` and a `## user` section, both Jinja2 templates rendered with
StrictUndefined (a missing variable is an error, never an empty string).
"""

import html
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined, Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)
_SECTION = re.compile(r"^##[ \t]+(system|user)[ \t]*$", re.MULTILINE)
_DOC_TAG = re.compile(r"<(\s*/?\s*document)", re.IGNORECASE)
TRUNCATED = " […truncated]"  # how untrusted() marks a cut text; callers may mark their own
_TRUNCATED = TRUNCATED

_env = Environment(undefined=StrictUndefined, autoescape=False)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: str


def load_prompt(name: str, *, directory: Path | None = None) -> Prompt:
    return _load(name, directory or PROMPTS_DIR)


@lru_cache(maxsize=128)
def _load(name: str, directory: Path) -> Prompt:
    text = (directory / f"{name}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = _FRONT_MATTER.match(text)
    if m is None:
        raise ValueError(f"prompt {name!r}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1))
    if not isinstance(meta, dict) or meta.get("version") in (None, ""):
        raise ValueError(f"prompt {name!r}: front matter must set `version`")

    parts = _SECTION.split(text[m.end() :])
    sections: dict[str, str] = {}
    for key, body in zip(parts[1::2], parts[2::2], strict=True):
        if key in sections:
            raise ValueError(f"prompt {name!r}: duplicate `## {key}` section")
        sections[key] = body.strip()
    missing = {"system", "user"} - sections.keys()
    if missing:
        raise ValueError(f"prompt {name!r}: missing section(s) {sorted(missing)}")
    for body in sections.values():
        _template(body)  # surface template syntax errors at load time
    return Prompt(name, str(meta["version"]), sections["system"], sections["user"])


@lru_cache(maxsize=256)
def _template(source: str) -> Template:
    return _env.from_string(source)


def render(prompt: Prompt, **variables: object) -> tuple[str, str]:
    """Render (system, user). Raises jinja2.UndefinedError on a missing variable."""
    system = _template(prompt.system).render(**variables).strip()
    user = _template(prompt.user).render(**variables).strip()
    return system, user


def neutralise_doc_tags(text: str) -> str:
    """Escape every `<document` / `</document` so the text cannot open or close a block."""
    return _DOC_TAG.sub(r"&lt;\1", text)


def untrusted(doc_id: int | str, text: str, max_chars: int = 6000) -> str:
    """Wrap scraped text in `<document id="…">` delimiters it cannot break out of."""
    body = text if len(text) <= max_chars else text[:max_chars] + _TRUNCATED
    body = neutralise_doc_tags(body)
    return f'<document id="{html.escape(str(doc_id), quote=True)}">{body}</document>'
