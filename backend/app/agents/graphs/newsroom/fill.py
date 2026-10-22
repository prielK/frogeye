"""Card text, copied from the signal's verified analysis.

No model writes any of it: the stage-B analysis was cited and fact-checked when it was
stored, and stage-A text was verified by the analyst, so nothing here is checked again.

With a current analysis:
- summary: `summary.text`;
- why_it_matters, with its `angle`: on an edition's takeaway, the role's own angle, the
  first non-empty of (sales) the first objection as "Objection: … — Response: …", else the
  first talk-track line as 'Say: "…"'; (pm) the first assessment; (exec) the first
  implication; (pmm_devrel) the first messaging line; angle = the role. Otherwise (the
  master, a section item, a role with no such text): the rationale of the highest-severity
  `jfrog_impact` entry (the first of equals; entries without a rationale are skipped), else
  (no product rated) the first competitive implication, else "" (the line is hidden);
  angle "jfrog";
- risk_level: on an exec edition's takeaway, the analysis's exec risk level (may be None);
- products: every `jfrog_impact` entry (product, effect, severity), in the analysis's order;
- actions: up to 3 of the edition role's flattened actions, less the one its angle shows
  (sales: talk track as 'Say: "…"', discovery questions, objections as
  "Objection: … → Response: …" (enclosing quotes the model wrote dropped),
  landmines; pm: assessment, follow-ups; exec: implications, decisions; pmm_devrel:
  messaging, content ideas). The master (role None) shows the first action of each role
  that has one, up to 3. A role with no actions shows none: the analysis says it has
  nothing to do.
Without one: the stage-A fact, impact (angle "jfrog") and role actions;
on an edition's takeaway the role's own stage-A line is why it matters instead
(angle = the role), and is not repeated under What to do. `from_analysis` says
which: True only when the body came from a current, parseable analysis, so the reading
surfaces can mark a stage-A card. `has_role_action` is an edition takeaway's eligibility:
an action for the role, in the analysis, or in stage A without one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agents.analysis_schema import Actions, Objection, SignalAnalysisContent
from app.agents.briefing_schema import EDITION_ROLES, ProductImpactRef, RoleAction
from app.agents.graphs.newsroom.selection import SignalView

MAX_ACTIONS = 3
FOLLOW_UP = "Follow-up: "
_SEVERITY = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class FilledItem:
    summary: str
    why_it_matters: str
    products: tuple[ProductImpactRef, ...]
    actions: tuple[RoleAction, ...]
    from_analysis: bool
    angle: str = "jfrog"  # where why_it_matters comes from (briefing_schema.Angle)
    risk_level: str | None = None  # exec takeaways only

    def fields(self) -> dict[str, Any]:
        """JSON-plain, for graph state and the persisted shape."""
        return {
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "products": [p.model_dump(mode="json") for p in self.products],
            "actions": [a.model_dump(mode="json") for a in self.actions],
            "from_analysis": self.from_analysis,
            "angle": self.angle,
            "risk_level": self.risk_level,
        }


_QUOTES = {'"': '"', "“": "”", "'": "'", "‘": "’"}


def _encloses(t: str) -> bool:
    """Whether the quote opening `t` is the one its last character closes, not one that
    closes earlier ('"A" and "B"' opens and closes twice). A closing mark between two
    letters is an apostrophe ("don’t"); a curly pair may nest inside."""
    opening, closing = t[0], t[-1]
    depth = 0
    for i in range(1, len(t) - 1):
        ch = t[i]
        if ch == closing and t[i - 1].isalnum() and t[i + 1].isalnum():
            continue  # an apostrophe, not a quote
        if ch == closing:
            if opening == closing or depth == 0:
                return False
            depth -= 1
        elif ch == opening:
            depth += 1
    return True


def unquote(text: str) -> str:
    """The text without ONE level of enclosing straight or curly quotes (the model
    sometimes quotes a line itself, and the reading surfaces add their own). Only quotes
    around the whole line go: '"A" and "B"' is kept as written (`_encloses`)."""
    t = text.strip()
    if len(t) >= 2 and _QUOTES.get(t[0]) == t[-1] and _encloses(t):
        return t[1:-1].strip()
    return t


def _say(line: str) -> str:
    """A talk-track line is what a seller says, so a card shows it as speech."""
    line = unquote(line)
    return f"Say: “{line}”" if line else ""


def _objection(o: Objection, joint: str = "→") -> str:
    objection, response = unquote(o.objection), unquote(o.response)
    if not objection:
        return ""
    return (
        f"Objection: {objection} {joint} Response: {response}"
        if response
        else f"Objection: {objection}"
    )


def _angle_objection(o: Objection) -> str:
    """An objection as the sales angle shows it: "Objection: … — Response: …"."""
    return _objection(o, "—")


def _verbatim(line: str) -> str:
    return line.strip()


# The angle's sources per role, in order: (the role section's field, how a card shows it).
_ANGLE_SOURCES: dict[str, tuple[tuple[str, Callable[[Any], str]], ...]] = {
    "sales": (("objections", _angle_objection), ("talk_track", _say)),
    "pm": (("assessment", _verbatim),),
    "exec": (("implications", _verbatim),),
    "pmm_devrel": (("messaging", _verbatim),),
}


def role_angle(actions: Actions, role: str) -> tuple[str, Actions]:
    """The role's angle: the text of the first non-empty entry of its sources (copied, as a
    card shows it), and the actions without that entry, so What to do does not repeat it.
    ("", actions) when the role has no text of its own."""
    section = getattr(actions, role)
    for name, show in _ANGLE_SOURCES[role]:
        entries = getattr(section, name)
        for n, entry in enumerate(entries):
            if text := show(entry):
                rest = section.model_copy(update={name: [*entries[:n], *entries[n + 1 :]]})
                return text, actions.model_copy(update={role: rest})
    return "", actions


def flattened_actions(actions: Actions, role: str) -> list[str]:
    """A role's analysis actions in reading order; empty entries are skipped."""
    if role == "sales":
        s = actions.sales
        texts = [
            *map(_say, s.talk_track),
            *s.discovery_questions,
            *map(_objection, s.objections),
            *s.landmines,
        ]
    elif role == "pm":
        texts = [*actions.pm.assessment, *actions.pm.follow_ups]
    elif role == "exec":
        texts = [*actions.exec.implications, *actions.exec.decisions]
    elif role == "pmm_devrel":
        texts = [*actions.pmm_devrel.messaging, *actions.pmm_devrel.content_ideas]
    else:
        raise ValueError(f"unknown edition role {role!r}")
    return [t.strip() for t in texts if t.strip()]


def _actions(by_role: dict[str, list[str]], role: str | None) -> tuple[RoleAction, ...]:
    if role is not None:
        return tuple(RoleAction(role=role, text=t) for t in by_role.get(role, [])[:MAX_ACTIONS])
    firsts = [RoleAction(role=r, text=by_role[r][0]) for r in EDITION_ROLES if by_role.get(r)]
    return tuple(firsts[:MAX_ACTIONS])


def has_role_action(view: SignalView, role: str) -> bool:
    """Whether `view` has an action for `role`: in its current analysis, or (without one)
    the stage-A line (an edition's takeaways need one)."""
    if view.analysis is not None:
        return bool(flattened_actions(view.analysis.recommended_actions, role))
    return any(r == role for r, _text in view.role_actions)


def fill_from_analysis(
    view: SignalView, analysis: SignalAnalysisContent | None, *, role: str | None
) -> FilledItem:
    """A card's body for one signal (see the module docstring). `role=None` is the master
    (and a section item, which is the master's in every edition)."""
    if analysis is None:
        by_role = {r: [text] for r, text in view.role_actions}
        # an edition's card angles on the role's own stage-A line, as it would on the
        # role's section of an analysis, and so does not repeat it under What to do.
        line = by_role.pop(role)[0] if role is not None and role in by_role else ""
        return FilledItem(
            summary=view.fact or view.title,
            why_it_matters=line or view.impact,
            products=(),
            actions=_actions(by_role, role),
            from_analysis=False,
            angle=role if role is not None and line else "jfrog",
        )
    rated = [i for i in analysis.jfrog_impact if i.rationale.strip()]
    top = min(rated, key=lambda i: _SEVERITY[i.severity], default=None)  # first of equals
    # Without a rated product, the stage-A impact line would claim an
    # impact the linked analysis says is not established: its first competitive
    # implication says why it matters instead, else the line is hidden ("").
    implication = next((c.text for c in analysis.competitive_implications if c.text.strip()), "")
    angle, why = "jfrog", top.rationale if top is not None else implication
    actions = analysis.recommended_actions
    if role is not None:
        text, actions = role_angle(actions, role)
        if text:
            angle, why = role, text
    exec_risk = analysis.recommended_actions.exec.risk_level
    return FilledItem(
        summary=analysis.summary.text,
        why_it_matters=why,
        products=tuple(
            ProductImpactRef(product=i.product, effect=i.effect, severity=i.severity)
            for i in analysis.jfrog_impact
            if i.product.strip()
        ),
        actions=_actions({r: flattened_actions(actions, r) for r in EDITION_ROLES}, role),
        from_analysis=True,
        angle=angle,
        risk_level=exec_risk if role == "exec" else None,
    )


def watch_note(view: SignalView, analysis: SignalAnalysisContent | None) -> str:
    """The first `what_to_watch` text of the analysis, else a follow-up on the title."""
    if analysis is not None:
        for w in analysis.what_to_watch:
            if w.text.strip():
                return w.text
    return f"{FOLLOW_UP}{view.title}"


# ---- cards (JSON-plain, as graph state carries them) -------------------------------------


def takeaway_card(view: SignalView, *, title: str, confidence: str, role: str | None) -> dict:
    """A top takeaway: the model's (checked) title over the copied body."""
    return {
        "title": title,
        "so_what": "",  # legacy fields, kept for briefings filed before
        "now_what": "",
        "signal_ids": [view.id],
        "confidence": confidence,
        **fill_from_analysis(view, view.analysis, role=role).fields(),
    }


def section_item(view: SignalView) -> dict:
    """A section row: the signal's title, its summary and why it matters. No role part, so
    an edition's item is the master's, verbatim."""
    filled = fill_from_analysis(view, view.analysis, role=None)
    return {
        "signal_id": view.id,
        "one_liner": view.title,
        "why_it_matters": filled.why_it_matters,
        "summary": filled.summary,
        "from_analysis": filled.from_analysis,
    }


def watch_item(view: SignalView) -> dict:
    return {"signal_id": view.id, "note": watch_note(view, view.analysis)}
