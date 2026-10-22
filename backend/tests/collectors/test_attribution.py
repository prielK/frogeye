import pytest

from app.collectors.attribution import display_title, source_label


@pytest.mark.parametrize(
    ("outlet", "expected"),
    [
        ("https://tech-insider.org/", "tech-insider.org via Google News"),
        ("www.example.com", "example.com via Google News"),
        ("http://www.example.com/path?x=1", "example.com via Google News"),
        ("example.org", "example.org via Google News"),
        ("ET Now", "ET Now via Google News"),
        ("The Register", "The Register via Google News"),
        ("https://", "Google News"),
        ("  ", "Google News"),
        (None, "Google News"),
        ("", "Google News"),
        ("http://[bad", "bad via Google News"),
        ("foo.[bar", "foo.bar via Google News"),
        ("https://[::1", "::1 via Google News"),
        ("a" * 5000 + ".com", "a" * 5000 + ".com via Google News"),
        ("https://münchen.example/", "münchen.example via Google News"),
        # a URL inside an outlet name keeps the name, the URL becomes its host
        ("Example News (https://www.example.com/x)", "Example News (example.com) via Google News"),
        ("Read at http://foo.org/a?b=1 today", "Read at foo.org today via Google News"),
        ("​", "​ via Google News"),
    ],
)
def test_source_label_never_shows_scheme(outlet, expected):
    label = source_label("google_news", "JFrog · Google News", {"outlet": outlet})
    assert label == expected
    assert "://" not in label


def test_source_label_gdelt_domain_and_plain_source():
    label = source_label("gdelt", "x", {"domain": "https://www.devops.com"})
    assert label == "devops.com via GDELT"
    assert source_label("rss", "Sonatype Blog", {"outlet": "https://a.b"}) == "Sonatype Blog"


def test_display_title_never_shows_a_url():
    """A document without a title, or whose title is a URL, reads like the Feed."""
    assert display_title("GitLab 19.4 ships", "https://x.test/a") == "GitLab 19.4 ships"
    assert display_title(None, "https://www.example.com/a") == "Untitled page on example.com"
    assert display_title("  ", "https://example.com/a") == "Untitled page on example.com"
    assert display_title("https://example.com/a", "https://example.com/a") == (
        "Untitled page on example.com"
    )
    assert display_title(None, "not a url") == "Untitled page"
