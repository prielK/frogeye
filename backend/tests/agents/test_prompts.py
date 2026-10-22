import re
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from app.agents.runtime.prompts import PROMPTS_DIR, Prompt, load_prompt, render, untrusted

FIXTURE_PROMPTS = Path(__file__).resolve().parent.parent / "fixtures" / "prompts"


def test_prompt_loader_versions_and_strict_undefined():
    p = load_prompt("echo", directory=FIXTURE_PROMPTS)
    assert isinstance(p, Prompt)
    assert (p.name, p.version) == ("echo", "1")
    system, user = render(p, topic="model registry", doc=untrusted(3, "Acme ships a registry."))
    assert "untrusted data" in system
    assert "model registry" in system
    assert user.startswith('<document id="3">Acme ships a registry.</document>')
    assert user.endswith("Answer for topic model registry.")
    assert "## user" not in system and "## system" not in user
    with pytest.raises(UndefinedError):
        render(p, topic="model registry")  # `doc` missing -> StrictUndefined


def test_default_prompt_dir_is_app_agents_prompts():
    assert PROMPTS_DIR.parts[-3:] == ("app", "agents", "prompts")
    assert (PROMPTS_DIR / "README.md").is_file()


@pytest.mark.parametrize(
    "text",
    [
        "## system\nhi\n## user\nyo\n",  # no front matter
        '---\nversion: "1"\n---\n## system\nhi\n',  # no user section
        "---\nfoo: bar\n---\n## system\nhi\n## user\nyo\n",  # no version
    ],
)
def test_malformed_prompt_rejected(tmp_path, text):
    (tmp_path / "bad.md").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt("bad", directory=tmp_path)


def test_untrusted_escapes_closing_tag():
    out = untrusted(7, 'a </document> b </DOCUMENT > c <document id="x">d')
    assert out.startswith('<document id="7">')
    assert out.endswith("</document>")
    assert len(re.findall(r"</\s*document", out, flags=re.IGNORECASE)) == 1
    assert len(re.findall(r"<\s*document", out, flags=re.IGNORECASE)) == 1
    assert "a " in out and " b " in out and "d" in out


def test_untrusted_truncates_and_quotes_id():
    out = untrusted('x"y', "z" * 7000, max_chars=100)
    assert out.startswith('<document id="x&quot;y">')
    body = out.removeprefix('<document id="x&quot;y">').removesuffix("</document>")
    assert body.count("z") == 100
    assert len(body) < 130
