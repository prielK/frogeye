"""A scripted Newsroom for FakeBackend, shared by the newsroom and daily-graph tests.

Every answer is a function of the request, never of the call order, so parallel Send
branches may run in any order. The newsroom's models write only the front (headline, lead,
takeaway titles) and the plan (themes, picks, order); card bodies come from the analyses.
An edition's takeaways are chosen in code: its adapter titles the ids its prompt lists.
"""

import json
import re

from tests.agents.fakes import Refusal

BOGUS = "BOGUS"
SILENT = "SILENT"
ADVICE = "ADVICE"
SENTENCE = re.compile(r"^(S\d+) \(cites signals [\d, ]+\): (.*)$", re.MULTILINE)
SIGNAL_LINE = re.compile(r"^Signal (\d+) \|", re.MULTILINE)
AUDIENCE_LINE = re.compile(r"^Audience: (\w+)", re.MULTILINE)
MASTER_BLOCK = re.compile(r'<document id="master">(.*?)</document>', re.DOTALL)
TAKEAWAYS_LINE = re.compile(r"^Takeaway signals, in order: (.*)$", re.MULTILINE)


def code(sid: int) -> str:
    """A digit-free tag for a signal id: fake texts must not trip the number guard."""
    return "".join("abcdefghij"[int(d)] for d in str(sid))


def outline_ids(master: dict) -> list[int]:
    """The signals of the master outline the adapter is shown: takeaways, then items."""
    ids = [t["signal_id"] for t in master["top_takeaways"]]
    ids += [sid for s in master["sections"] for sid in s["signal_ids"]]
    return list(dict.fromkeys(ids))


# ---- scripted newsroom ------------------------------------------------------------------


def chosen_ids(user: str) -> list[int]:
    """The code-chosen takeaway signals an adapter prompt lists, in order."""
    listed = TAKEAWAYS_LINE.search(user).group(1).strip()
    return [] if listed == "none" else [int(x) for x in listed.split(", ")]


def edition_title(role: str, sid: int) -> str:
    """The default adapter's title for a chosen takeaway (digit-free)."""
    return f"{role} takeaway {code(sid)}"


def default_adapter(role: str, master: dict, chosen: list[int]) -> dict:
    """An edition with its own front, a title for each code-chosen takeaway (in order) and
    every master signal picked in reverse order. With nothing chosen the schema offers no
    `top_takeaways` field, so none is sent."""
    out = {
        "headline": f"{role} headline",
        "lead": f"{role} lead",
        "pick": list(reversed(outline_ids(master))),
    }
    if chosen:
        out["top_takeaways"] = [{"signal_id": i, "title": edition_title(role, i)} for i in chosen]
    return out


class Newsroom:
    """Order-independent FakeBackend script.

    The fact-checker marks exactly the sentences containing BOGUS as unsupported, classifies
    those containing ADVICE as advice, returns no verdict at all for SILENT ones and refuses
    checks whose prompt has `fail_check_when` (or the very first check with
    `fail_first_check`).
    """

    def __init__(
        self,
        plan,
        *,
        adapter=default_adapter,
        fail_first_check=False,
        fail_check_when=None,
    ):
        self.plan = plan
        self.adapter = adapter
        self.fail_first_check = fail_first_check
        self.fail_check_when = fail_check_when
        self.checks = 0

    def script(self) -> dict:
        return {
            "editor": self.editor,
            "fact_checker": self.checker,
            "role_adapter": self.role_adapter,
        }

    def editor(self, *, kind, **_):
        return "No tools needed." if kind == "tool_turn" else self.plan

    def checker(self, *, user, **_):
        self.checks += 1
        if self.fail_first_check and self.checks == 1:
            return Refusal("cannot check")
        if self.fail_check_when and self.fail_check_when in user:
            return Refusal("cannot check")
        return {
            "verdicts": [
                {
                    "sentence_id": sid,
                    "kind": "advice" if ADVICE in text else "claim",
                    "supported": BOGUS not in text,
                    "reason": "unsupported claim" if BOGUS in text else "supported",
                }
                for sid, text in SENTENCE.findall(user)
                if SILENT not in text
            ]
        }

    def role_adapter(self, *, user, **_):
        role = AUDIENCE_LINE.search(user).group(1)
        master = json.loads(MASTER_BLOCK.search(user).group(1))
        return self.adapter(role, master, chosen_ids(user))


def takeaway(sid, title, **kw):
    out = {"signal_id": sid, "title": title, "confidence": "high"}
    out.update(kw)
    return out


def make_plan(assignment, *, takeaways=None, watchlist=None, **kw):
    """An editor plan: `assignment` is [(signal id, theme)], `watchlist` signal ids."""
    first = assignment[0][0]
    plan = {
        "headline": "Rivals reprice and ship supply-chain features",
        "lead": "Two rivals moved on price and product; JFrog should answer on both.",
        "top_takeaways": takeaways if takeaways is not None else [takeaway(first, "Lead story")],
        "theme_assignment": [{"signal_id": s, "theme": t} for s, t in assignment],
        "watchlist": [{"signal_id": s} for s in (watchlist or [])],
    }
    plan.update(kw)
    return plan
