"""Who may see which signals, shared by the routers that list or search them.

Non-analysts only ever see status auto|approved (list, search, detail, related, agent-step
results); analysts default to auto|approved|needs_review and may ask for any status.
"""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_

from app.models import Signal, User
from app.models.enums import Role, SignalStatus

PUBLIC_STATUSES = (SignalStatus.auto.value, SignalStatus.approved.value)
ANALYST_STATUSES = (*PUBLIC_STATUSES, SignalStatus.needs_review.value)
# news about JFrog itself is hidden from every role, analysts included.
HIDDEN_SCOPES = ("jfrog",)


def is_analyst(user: User) -> bool:
    return str(user.role) == Role.analyst.value


def visible_statuses(
    user: User, requested: Sequence[SignalStatus | str] | None = None
) -> list[str]:
    """The statuses this user may list. A non-analyst's request is intersected with
    auto|approved (possibly to nothing); an analyst gets exactly what they asked for."""
    asked = [str(s) for s in requested] if requested else None
    if is_analyst(user):
        return asked or list(ANALYST_STATUSES)
    return [s for s in (asked or PUBLIC_STATUSES) if s in PUBLIC_STATUSES]


def visible_signal_clause(user: User) -> ColumnElement[bool]:
    """A signal this user may see NOW: its status is in `visible_statuses(user)` and its
    scope is not hidden (JFrog-self news never shows, whatever the status or role)."""
    return and_(
        Signal.status.in_(visible_statuses(user)),
        Signal.scope.not_in(HIDDEN_SCOPES),
    )


def public_signal_clause() -> ColumnElement[bool]:
    """The ONE user-independent rule for every intel aggregate, brief input and
    deep-dive section: status auto|approved and scope not hidden. The deep dives read the same
    for every persona, so an analyst's needs_review signals never move a threat, overlap or
    momentum number (signal cards still filter with `visible_signal_clause(user)`)."""
    return and_(
        Signal.status.in_(PUBLIC_STATUSES),
        Signal.scope.not_in(HIDDEN_SCOPES),
    )
