"""JFrog-self news: ONE predicate for the documents that are news about JFrog itself.

The product decision is "drop it entirely". Signals carry it as `scope = 'jfrog'` (hidden on
every read path); raw documents carry it in four ways, all from before the self-news rules existed
or from a self-tied feed, since migration 0006 removed the document-company links that once said it:
- prefilter tagged the document scope "jfrog" (`documents.scope_hint`);
- prefilter rejected it because it named only JFrog (`prefilter->>'reason' = 'jfrog_only'`);
- triage judged its scope "jfrog" (`triage->>'scope'`), the legacy members of the
  suppressed signals included;
- it came from a source tied to the self competitor (JFrog's own feeds), except
  `github_repo_stats`, which writes metrics, not documents.
The Feed and the article hits of /search use it; JFrog's own product docs
(retrieval kind jfrog_kb, also indexed with scope "jfrog") are not documents and stay.
"""

from functools import cache

from sqlalchemy import ColumnElement, false, func, or_, select
from sqlalchemy.dialects import postgresql

from app.models import Competitor, Document, Source
from app.models.enums import Scope, SourceKind
from app.processing.prefilter import JFROG_ONLY


def self_news_document() -> ColumnElement[bool]:
    """True for a JFrog-self news document (never NULL, so `~` of it is safe in a WHERE)."""
    self_sources = (
        select(Source.id)
        .join(Competitor, Competitor.id == Source.competitor_id)
        .where(Competitor.is_self.is_(True), Source.kind != SourceKind.github_repo_stats)
    )
    return or_(
        func.coalesce(Document.scope_hint == Scope.jfrog.value, false()),
        func.coalesce(Document.prefilter["reason"].astext == JFROG_ONLY, false()),
        func.coalesce(Document.triage["scope"].astext == Scope.jfrog.value, false()),
        Document.source_id.in_(self_sources),
    )


@cache
def self_news_document_ids_sql() -> str:
    """`SELECT documents.id FROM documents WHERE <the predicate>` as SQL text, for the
    hybrid search's raw SQL (rag.search), so both readers share one definition."""
    stmt = select(Document.id).where(self_news_document())
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
