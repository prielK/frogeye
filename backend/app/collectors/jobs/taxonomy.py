"""Rule-based department and seniority for job postings (exact lists).

The title decides first: it is more specific than an ATS department ("R&D" holds security
researchers and data scientists alike). The board's own department is the fallback, then
"Other". Rules are ordered: the first that matches wins, so specific roles ("Sales Engineer",
"Developer Advocate", "Product Marketing Manager") sit above the broad words they contain.
UI and UX engineers count as Design (a deliberate choice, recorded in the tests).
"""

import re

DEPARTMENTS = (
    "Engineering",
    "Security",
    "Product",
    "Design",
    "Data & ML",
    "Sales",
    "Solutions & SE",
    "Customer success",
    "Marketing",
    "DevRel & community",
    "G&A",
    "Other",
)
SENIORITIES = (
    "Intern",
    "Junior",
    "Mid",
    "Senior",
    "Staff & Principal",
    "Manager",
    "Director",
    "VP & C-level",
)


def _rx(*alternatives: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")", re.IGNORECASE)


_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "G&A",
        _rx(
            r"accountant",
            r"accounting",
            r"accounts? (?:payable|receivable)",
            r"financial controller",
            r"payroll",
            r"treasury",
            r"tax\b",
            r"legal\b",
            r"counsel\b",
            r"paralegal",
            r"attorney",
            r"people (?:ops|operations|partner|business partner|experience|team)",
            r"(?:head|vp|director),? (?:of )?(?:people|talent)\b",
            r"human resources",
            r"hr\b",
            r"hrbp",
            r"recruit",
            r"talent (?:acquisition|partner|sourcer)",
            r"sourcer\b",
            r"office (?:manager|coordinator|admin)",
            r"workplace",
            r"facilities",
            r"procurement",
            r"executive assistant",
            r"administrative",
            r"it (?:support|help ?desk|administrator|manager|specialist|operations)",
            r"help ?desk",
            r"chief (?:financial|people|legal|operating|executive) officer",
            r"c[efo]o\b",
            r"business operations",
            r"bizops\b",
            r"strategy (?:&|and) operations",
        ),
    ),
    (
        "Solutions & SE",
        _rx(
            r"sales engineer",
            r"solutions? (?:engineer|architect|consultant|specialist)",
            r"pre-?sales",
            r"field (?:engineer|cto)",
            r"customer engineer",
            r"value engineer",
            r"technical (?:sales|solutions)",
            r"demo engineer",
        ),
    ),
    (
        "DevRel & community",
        _rx(
            r"developer (?:relations|advocate|advocacy|evangelist)",
            r"devrel",
            r"dev rel",
            r"evangelist",
            r"community\b",
            r"open source program",
            r"ospo\b",
        ),
    ),
    (
        "Customer success",
        _rx(
            r"customer success",
            r"csm\b",
            r"customer support",
            r"technical support",
            r"support (?:engineer|specialist|analyst|manager|lead|team|agent|representative)",
            r"technical account manager",
            r"tam\b",
            r"customer (?:experience|care|operations|enablement)",
            r"onboarding",
            r"implementation (?:engineer|consultant|manager|specialist)",
            r"professional services",
            r"renewals?\b",
        ),
    ),
    (
        "Sales",
        _rx(
            r"account executive",
            r"ae\b",
            r"sales\b",
            r"business development",
            r"bdr\b",
            r"sdr\b",
            r"account (?:manager|director|development)",
            r"partnerships?\b",
            r"partner (?:manager|lead|director|marketing)",
            r"channel\b",
            r"alliances?\b",
            r"revenue operations",
            r"revops\b",
            r"deal desk",
            r"chief revenue officer",
            r"cro\b",
            r"territory",
            r"go[- ]to[- ]market",
            r"gtm\b",
        ),
    ),
    (
        "Marketing",
        _rx(
            r"marketing",
            r"marketer",
            r"brand\b",
            r"content (?:marketing|writer|strategist|manager|lead|designer)",
            r"copywriter",
            r"communications",
            r"public relations",
            r"pr\b",
            r"demand gen",
            r"growth (?:market|manager|lead)",
            r"seo\b",
            r"social media",
            r"events? (?:manager|marketing|coordinator|lead|specialist)",
            r"campaign",
            r"chief marketing officer",
            r"cmo\b",
        ),
    ),
    (
        "Design",
        _rx(
            r"designer",
            r"design\b",
            r"ux\b",
            r"ui\b",
            r"user (?:research|experience)",
        ),
    ),
    (
        "Product",
        _rx(
            r"product (?:manager|owner|management|lead|director|operations|ops|analyst)",
            r"head of product",
            r"vp,? (?:of )?product",
            r"director,? (?:of )?product",
            r"chief product officer",
            r"cpo\b",
            r"pm\b",
            r"apm\b",
        ),
    ),
    (
        "Security",
        _rx(
            r"security",
            r"secops\b",
            r"vulnerabilit",
            r"threat",
            r"malware",
            r"appsec",
            r"penetration",
            r"pen ?test",
            r"offensive",
            r"red team",
            r"blue team",
            r"soc\b",
            r"incident response",
            r"c?iso\b",
            r"cso\b",
            r"cryptograph",
            r"exploit",
            r"detection (?:engineer|researcher)",
            r"forensic",
            r"grc\b",
        ),
    ),
    (
        "Data & ML",
        _rx(
            r"data (?:scien|engineer|analy|platform|infrastructure)",
            r"machine learning",
            r"ml\b",
            r"mlops",
            r"ai\b",
            r"artificial intelligence",
            r"llm",
            r"deep learning",
            r"nlp\b",
            r"computer vision",
            r"analytics",
            r"research scientist",
            r"applied scientist",
            r"business intelligence",
            r"bi\b",
        ),
    ),
    (
        "Engineering",
        _rx(
            r"engineer",
            r"developer",
            r"software",
            r"sre\b",
            r"site reliability",
            r"devops",
            r"back-?end",
            r"front-?end",
            r"full-?stack",
            r"qa\b",
            r"quality (?:assurance|engineer)",
            r"test automation",
            r"sdet\b",
            r"architect",
            r"programmer",
            r"infrastructure",
            r"platform",
            r"tech(?:nical)? lead",
            r"r&d",
            r"firmware",
            r"mobile",
            r"ios\b",
            r"android",
            r"cto\b",
            r"chief technology officer",
            r"technical writer",
            r"documentation",
            r"researcher",
        ),
    ),
)

# a finance word makes a title G&A ("Financial Auditor", "FP&A Lead", and over
# a broader word: "Financial Systems Engineer"), except in a sales or SE role ("Account
# Executive, Financial Services", "Solutions Engineer - Finance") and where a vertical word
# says the finance is a market ("Banking & Finance", "Finance and Insurance", "Financial
# Services Industry Lead").
_FINANCE = _rx(r"financ", r"fp&a")
_VERTICAL = _rx(r"banking", r"insurance", r"services", r"institutions?", r"industry")
_FINANCE_KEEPS = frozenset({"G&A", "Sales", "Solutions & SE"})
# a finance ROLE inside a GTM org is still finance ("Sales Finance Manager", "Senior
# Manager, GTM Finance", "FP&A Manager, Go-to-Market"): FP&A, a controller, accounting or a
# finance manager or lead wins over Sales, and makes a title the other rules left without a
# department G&A ("Revenue Accountant"). A sales role in a finance market keeps Sales
# ("Account Executive, Financial Services"): none of these words is in it.
_FINANCE_ROLE = _rx(
    r"fp&a",
    r"controller",
    r"accounting",
    r"accountant",
    r"financ(?:e|ial) (?:manager|lead|director|partner|analyst|controller|planning)",
    r"(?:sales|gtm|go[- ]to[- ]market|revenue|commercial) finance",
)

# A board's department names are broader than titles: words that only mean something there.
_RAW_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("G&A", _rx(r"g&a", r"general (?:&|and) admin", r"people\b", r"operations", r"admin", r"it\b")),
    ("Engineering", _rx(r"research", r"technology", r"development", r"tech\b")),
    ("Data & ML", _rx(r"data\b")),
    ("Product", _rx(r"product")),
    ("Customer success", _rx(r"customer", r"support", r"services")),
    ("Sales", _rx(r"revenue", r"commercial", r"field\b")),
)


def _match(text: str, rules: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    for department, pattern in rules:
        if pattern.search(text):
            return department
    return None


def _title_department(text: str) -> str | None:
    """The title rules, then the finance rules: a finance role (`_FINANCE_ROLE`) over Sales
    and no department, a finance word (`_FINANCE`) over all but G&A, Sales and Solutions &
    SE."""
    department = _match(text, _TITLE_RULES)
    if department in (None, "Sales") and _FINANCE_ROLE.search(text):
        return "G&A"
    if department in _FINANCE_KEEPS or not _FINANCE.search(text) or _VERTICAL.search(text):
        return department
    return "G&A"


def normalize_department(raw: str | None, title: str) -> str:
    """One of DEPARTMENTS: from the title, else the board's department, else "Other"."""
    for text, rules in ((title, None), (raw, None), (raw, _RAW_RULES)):
        if text:
            department = _title_department(text) if rules is None else _match(text, rules)
            if department:
                return department
    return "Other"


_INTERN = _rx(
    r"intern\b", r"internship", r"co-?op\b", r"working student", r"apprentice", r"trainee"
)
_C_LEVEL = _rx(
    r"vp\b",
    r"[se]vp\b",
    r"vice[- ]president",
    r"chief(?! of staff)",
    r"c[a-z]{1,2}o\b",
    r"president",
    r"co-?founder",
    r"founder",
)
_DIRECTOR = _rx(r"director", r"head of", r"head,", r"general manager", r"chief of staff")
_STAFF = _rx(r"staff(?! accountant)\b", r"principal", r"distinguished", r"fellow\b")
_MANAGER = _rx(r"manager", r"supervisor", r"team lead", r"group lead")
# "Product Manager" and friends are individual contributors, not people managers.
_IC_MANAGER = _rx(
    r"(?:product|program|project|account|success|marketing|partner|partnerships|community|"
    r"campaign|content|events?|territory|release|channel|alliances?|brand|social media|"
    r"communications|pr|seo|growth|office|category|vendor|contracts?|payroll|facilities|it|"
    r"solutions?|enablement|operations) manager"
)
# "lead" and "leader" as words ("Technical Leader" is senior); "Leadership" and
# "Leading" say nothing of seniority.
_SENIOR = _rx(r"senior", r"sr\b", r"lead(?:er)?\b(?! gen)", r"iii\b", r"iv\b")
_JUNIOR = _rx(
    r"junior",
    r"jr\b",
    r"associate",
    r"entry[- ]level",
    r"graduate",
    r"new grad",
    r"(?:engineer|developer|analyst|scientist|specialist|designer|representative) i\b(?!i)",
)


def seniority_of(title: str) -> str:
    """One of SENIORITIES, from the title alone; "Mid" when it names no level."""
    if _INTERN.search(title):
        return "Intern"
    if _C_LEVEL.search(title):
        return "VP & C-level"
    if _DIRECTOR.search(title):
        return "Director"
    if _STAFF.search(title):
        return "Staff & Principal"
    if _MANAGER.search(title) and not _IC_MANAGER.search(title):
        return "Manager"
    if _SENIOR.search(title):
        return "Senior"
    if _JUNIOR.search(title):
        return "Junior"
    return "Mid"
