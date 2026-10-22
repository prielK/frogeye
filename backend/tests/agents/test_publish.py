"""Briefing delivery text: the email and the Slack post render the card
fields copied from the analysis (summary, why it matters, products, actions) and fall back to
the legacy so_what / now_what / why_it_matters of briefings filed before them."""

from datetime import date

import pytest

from app.agents.briefing_schema import BriefingContent
from app.agents.nodes.publish import first_sentence, render_email, slack_text

DAY = date(2026, 10, 18)
APP = "https://frogeye.example.test"


def new_content() -> BriefingContent:
    return BriefingContent.model_validate(
        {
            "headline": "Rivals move on price",
            "lead": "A calm lead.",
            "top_takeaways": [
                {
                    "title": "Registry race",
                    "signal_ids": [1],
                    "confidence": "high",
                    "summary": "Sonatype launched a registry. It scans <script>x</script> models.",
                    "why_it_matters": "It competes with JFrog ML model management.",
                    "products": [
                        {"product": "JFrog ML", "effect": "threat", "severity": "high"},
                        {"product": "Xray", "effect": "opportunity", "severity": "low"},
                    ],
                    "actions": [
                        {"role": "sales", "text": "Lead with model governance."},
                        {"role": "pm", "text": "Check registry parity."},
                        {"role": "exec", "text": "Decide whether to match the bundle."},
                    ],
                },
                {
                    "title": "Quiet card",
                    "signal_ids": [2],
                    "confidence": "medium",
                    "summary": "GitLab changed a price.",
                    "why_it_matters": "",
                    "products": [],
                    "actions": [],
                },
            ],
            "sections": [
                {
                    "theme": "Industry pulse",
                    "items": [
                        {
                            "signal_id": 3,
                            "one_liner": "CISA updates SBOM guidance",
                            "summary": "CISA published new minimum elements. Vendors follow later.",
                            "why_it_matters": "Raises the bar for SBOM export.",
                        }
                    ],
                }
            ],
            "quiet_competitors": [],
            "watchlist": [{"signal_id": 3, "note": "Whether CISA sets a deadline."}],
        }
    )


def legacy_content() -> BriefingContent:
    """A briefing filed before the analysis-backed cards: so_what / now_what only, and a
    legacy "JFrog in the news" section."""
    return BriefingContent.model_validate(
        {
            "headline": "Rivals move on price",
            "lead": "A calm lead.",
            "top_takeaways": [
                {
                    "title": "Price gap",
                    "so_what": "Deals get harder.",
                    "now_what": "Ask about model provenance.",
                    "signal_ids": [1],
                    "confidence": "high",
                },
                {
                    "title": "Registry race",
                    "so_what": "Models need governance.",
                    "now_what": "",
                    "signal_ids": [2],
                    "confidence": "medium",
                },
            ],
            "sections": [
                {
                    "theme": "JFrog in the news",
                    "items": [
                        {"signal_id": 3, "one_liner": "Old one-liner", "why_it_matters": "Old why."}
                    ],
                }
            ],
            "quiet_competitors": [],
            "watchlist": [],
        }
    )


def test_email_renders_new_fields_and_legacy():
    _, html, plain = render_email(new_content(), day=DAY, edition="master", names={}, app_url=APP)
    assert "Sonatype launched a registry." in html and "&lt;script&gt;" in html
    assert "<script>" not in html
    assert html.count("Why it matters for JFrog") == 1  # hidden for the card without one
    assert "It competes with JFrog ML model management." in html
    assert "JFrog ML · Threat · high" in html and "Xray · Opportunity · low" in html
    assert html.count("What to do") == 1  # hidden for the card without actions
    for label, text in (
        ("Sales", "Lead with model governance."),
        ("Product", "Check registry parity."),
        ("Executive", "Decide whether to match the bundle."),
    ):
        assert f"{label}</span> · {text}" in html  # the master labels each action's role
        assert f"- {label}: {text}" in plain
    assert "Now what" not in html and "Now what" not in plain
    # A section row is its title and the summary's first sentence.
    assert "CISA published new minimum elements." in html
    assert "Vendors follow later." not in html and "Raises the bar" not in html
    assert f'href="{APP}/signals/3"' in html  # the story's full analysis page
    assert "Sonatype launched a registry." in plain
    assert "Why it matters for JFrog: It competes with JFrog ML model management." in plain
    assert "Products: JFrog ML (threat, high), Xray (opportunity, low)" in plain
    assert "- CISA updates SBOM guidance: CISA published new minimum elements." in plain

    # A role edition's actions are all its own role's: no labels.
    _, html, plain = render_email(new_content(), day=DAY, edition="sales", names={}, app_url=APP)
    assert "Lead with model governance." in html and "Sales</span> ·" not in html
    assert f'href="{APP}/signals/3?edition=sales"' in html  # opens on that role's actions
    assert "- Lead with model governance." in plain and "- Sales:" not in plain

    # Legacy content renders so_what as the body and now_what as its single action.
    _, html, plain = render_email(legacy_content(), day=DAY, edition="master", names={})
    assert "Deals get harder." in html and "Models need governance." in html
    assert html.count("Now what") == 1  # the empty now_what stays hidden
    assert "Ask about model provenance." in html
    assert "What to do" not in html and "Why it matters for JFrog" not in html
    assert "Old one-liner" in html and "Old why." in html  # legacy items keep their reason
    assert "- Price gap: Deals get harder." in plain
    assert "Now what: Ask about model provenance." in plain and "Now what: \n" not in plain


ANGLE_LABELS = {
    "jfrog": "Why it matters for JFrog",
    "sales": "In your deals",
    "pm": "Product angle",
    "exec": "Strategic angle",
    "pmm_devrel": "Messaging angle",
}


def angled_content(angle: str, risk_level: str | None = None) -> BriefingContent:
    """An edition whose first card's why_it_matters comes from `angle`'s analysis section."""
    data = new_content().model_dump(mode="json")
    data["top_takeaways"][0].update(
        angle=angle, risk_level=risk_level, why_it_matters=f"The {angle} angle."
    )
    return BriefingContent.model_validate(data)


@pytest.mark.parametrize("angle", list(ANGLE_LABELS))
def test_email_shows_angle_label_and_exec_risk(angle):
    """Role editions: the why-it-matters line is labelled with its angle (the same copy as
    the app), and an exec card shows its risk level as a word with an icon, never colour."""
    edition = "master" if angle == "jfrog" else angle
    risk = "high" if angle == "exec" else None
    _, html, plain = render_email(
        angled_content(angle, risk), day=DAY, edition=edition, names={}, app_url=APP
    )
    label = ANGLE_LABELS[angle]
    assert f"{label}</span> · The {angle} angle." in html
    assert f"  {label}: The {angle} angle." in plain
    others = [v for k, v in ANGLE_LABELS.items() if k != angle]
    assert not any(o in html or o in plain for o in others)  # one label per card
    if angle == "exec":
        assert "&#9650; Risk: high</span>" in html  # icon plus word
        assert "  Risk: high" in plain
    else:
        assert "Risk:" not in html and "Risk:" not in plain


def test_email_risk_levels_and_legacy_angle():
    for level, icon in (("high", "&#9650;"), ("medium", "&#9670;"), ("low", "&#9660;")):
        _, html, plain = render_email(
            angled_content("exec", level), day=DAY, edition="exec", names={}, app_url=APP
        )
        assert f"{icon} Risk: {level}</span>" in html and f"  Risk: {level}" in plain
    # An exec card without a risk level shows no chip; a legacy row reads the jfrog label.
    _, html, _ = render_email(angled_content("exec"), day=DAY, edition="exec", names={})
    assert "Risk:" not in html
    legacy = new_content().model_dump(mode="json")
    for t in legacy["top_takeaways"]:
        del t["angle"], t["risk_level"]
    content = BriefingContent.model_validate(legacy)
    assert {(t.angle, t.risk_level) for t in content.top_takeaways} == {("jfrog", None)}
    _, html, _ = render_email(content, day=DAY, edition="sales", names={})
    assert html.count("Why it matters for JFrog") == 1


def test_slack_text_gives_each_takeaway_its_gist():
    msg = slack_text(new_content(), day=DAY, app_url=APP)
    assert f"<{APP}/signals/1|Registry race> — Sonatype launched a registry." in msg
    assert "script" not in msg  # only the first sentence, escaped
    assert f"<{APP}/signals/3|CISA updates SBOM guidance>" in msg
    legacy = slack_text(legacy_content(), day=DAY, app_url=APP)
    assert f"<{APP}/signals/1|Price gap> — Deals get harder." in legacy


@pytest.mark.parametrize(
    ("text", "first"),
    [
        # dates, regions, times and ranks stay whole (the frontend's
        # firstSentence keeps the same cases, frontend/src/features/briefing/story.ts)
        (
            "It reaches GA on Oct. 15 for Premium tiers. Pricing follows.",
            "It reaches GA on Oct. 15 for Premium tiers.",
        ),
        (
            "It runs in the U.S. East region. More regions later.",
            "It runs in the U.S. East region.",
        ),
        (
            "The webinar starts at 9 a.m. ET on Tuesday. Register now.",
            "The webinar starts at 9 a.m. ET on Tuesday.",
        ),
        ("Ranked No. 1 by analysts. The rest trail.", "Ranked No. 1 by analysts."),
        ("Acme Inc. Ships X today. Then more.", "Acme Inc. Ships X today."),
        ("Tools, e.g. Nexus, gain it. Others wait.", "Tools, e.g. Nexus, gain it."),
        ("Version 2.0 ships. Next.", "Version 2.0 ships."),
        ('He said "go." Then left.', 'He said "go."'),
        ("Why now? Because.", "Why now?"),
        ("No full stop here", "No full stop here"),
        ("  Spread\nover lines. Second.", "Spread over lines."),
        ("", ""),
    ],
)
def test_first_sentence_keeps_abbreviations_whole(text, first):
    assert first_sentence(text) == first


def test_email_counts_every_signal_the_briefing_shows():
    """The stats line counts what the app's masthead counts: every signal the edition
    shows (takeaways, sections and watchlist), so an edition of takeaways only is not "0"."""
    funnel = {"fetched": 120, "relevant": 14}
    _, html, _ = render_email(new_content(), day=DAY, edition="master", names={}, funnel=funnel)
    assert "Scanned 120 · 14 relevant · 3 in this briefing" in html

    data = new_content().model_dump(mode="json")
    data["sections"] = []  # a role edition's shape: takeaways and a watch note
    data["watchlist"][0]["signal_id"] = 4
    edition = BriefingContent.model_validate(data)
    _, html, _ = render_email(edition, day=DAY, edition="sales", names={}, funnel=funnel)
    assert "3 in this briefing" in html and "0 in this briefing" not in html
