import { useEffect, useEffectEvent, useState } from "react";

export const SEARCH_DEBOUNCE_MS = 300;

/**
 * A search box bound to a term kept in the URL: returns the box's draft text and its setter;
 * the trimmed draft is committed `delay` ms after typing stops (as the Feed's box does).
 *
 * The URL can change the term too (Clear filters, back/forward), and then the box follows it.
 * Our own debounced commit must not: it is trimmed ("jfrog " commits "jfrog") and the router
 * applies it in a transition, after further keystrokes may have landed. So a URL change resets
 * the box only when it differs from the term this box last committed.
 */
export function useDebouncedSearch(urlQ: string, commit: (q: string) => void, delay = SEARCH_DEBOUNCE_MS) {
  const [draft, setDraft] = useState(urlQ);
  const [seenQ, setSeenQ] = useState(urlQ);
  const [committedQ, setCommittedQ] = useState(urlQ);
  if (urlQ !== seenQ) {
    setSeenQ(urlQ);
    if (urlQ !== committedQ) {
      setCommittedQ(urlQ);
      setDraft(urlQ);
    }
  }

  const onCommit = useEffectEvent((q: string) => {
    setCommittedQ(q);
    commit(q);
  });
  useEffect(() => {
    const next = draft.trim();
    if (next === urlQ) return;
    const timer = setTimeout(() => onCommit(next), delay);
    return () => clearTimeout(timer);
  }, [draft, urlQ, delay]);

  return [draft, setDraft] as const;
}
