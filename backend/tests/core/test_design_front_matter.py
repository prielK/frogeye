"""DESIGN.md's front matter is strict YAML.

The design tooling reads the front matter as YAML; a colon inside an unquoted value (the
`description:` bug) or a key given twice silently breaks or changes it. The repo root holds
DESIGN.md; the test stack mounts it read-only at /DESIGN.md (docker-compose.test.yml), CI reads it
from the checkout.
"""

from pathlib import Path

import pytest
import yaml


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a mapping key given twice (PyYAML keeps the last silently)."""


def _no_duplicates(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def _design_md() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "DESIGN.md", Path("/DESIGN.md")):
        if candidate.is_file():
            return candidate
    raise AssertionError(
        "DESIGN.md not found: mount it in the test stack (docker-compose.test.yml)"
    )


def front_matter(text: str) -> dict:
    head, sep, rest = text.partition("---\n")
    assert head == "" and sep, "DESIGN.md starts with its front matter"
    body, sep, _ = rest.partition("\n---\n")
    assert sep, "the front matter ends with ---"
    return yaml.load(body, Loader=StrictLoader)


def test_design_md_front_matter_is_strict_yaml():
    data = front_matter(_design_md().read_text(encoding="utf-8"))
    assert data["name"] == "FrogEye"
    assert isinstance(data["description"], str) and ": " in data["description"]
    for group in ("colors", "typography", "rounded", "spacing", "components"):
        assert isinstance(data[group], dict) and data[group], group
    assert all(isinstance(v, str) for v in data["colors"].values())


@pytest.mark.parametrize(
    "broken",
    [
        # the bug: an unquoted colon in a value
        "name: FrogEye\ndescription: Briefing pages: sharp, calm\n",
        # a key given twice
        "name: FrogEye\nname: Again\n",
    ],
)
def test_the_strict_loader_catches_what_broke_before(broken):
    with pytest.raises(yaml.YAMLError):
        front_matter(f"---\n{broken}\n---\nbody\n")
