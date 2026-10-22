import { useLocation, useNavigate, type Location, type NavigateFunction } from "react-router";

/*
 * Which signal the app-level drawer (the quick look) shows: `?signal=<id>` on whatever route
 * the reader is on. Opening adds the parameter to the current location, closing removes it;
 * nothing navigates away. This module is the one seam that decides the open signal. The full
 * analysis is a page of its own, `/signals/:id` (features/analysis), which the quick look links to.
 */

export const SIGNAL_PARAM = "signal";

/** The open signal's id from a location search, or null. */
export function parseSignalParam(search: string): number | null {
  const raw = new URLSearchParams(search).get(SIGNAL_PARAM);
  return raw && /^\d{1,9}$/.test(raw) ? Number(raw) : null;
}

/** `search` with the signal parameter set to `id` (or removed for null); other parameters are kept. */
export function withSignalParam(search: string, id: number | null): string {
  const params = new URLSearchParams(search);
  if (id === null) params.delete(SIGNAL_PARAM);
  else params.set(SIGNAL_PARAM, String(id));
  const query = params.toString();
  return query ? `?${query}` : "";
}

/**
 * The full analysis page of signal `id`. Every link into the page goes through here; the page
 * opens the reader's own role's actions first, as every page does (each reader sees only their
 * own edition).
 */
export function analysisHref(id: number): string {
  return `/signals/${id}`;
}

type DrawerState = { drawer?: boolean } | null;

/**
 * Open `id` over `location`. From a closed drawer this pushes an entry marked `drawer`, so
 * Back (or Close) returns to the page as it was. With a drawer already open (a related
 * signal) it replaces the entry and keeps its mark: a drawer reached by a link has no entry
 * of its own to go back past.
 */
export function openSignalAt(navigate: NavigateFunction, location: Location, id: number) {
  const target = { pathname: location.pathname, search: withSignalParam(location.search, id), hash: location.hash };
  if (parseSignalParam(location.search) !== null) navigate(target, { replace: true, state: location.state });
  else navigate(target, { state: { drawer: true } });
}

/** Close the drawer: step back over the entry opening pushed, else drop the parameter in place. */
export function closeSignalAt(navigate: NavigateFunction, location: Location) {
  if ((location.state as DrawerState)?.drawer) {
    navigate(-1);
    return;
  }
  navigate(
    { pathname: location.pathname, search: withSignalParam(location.search, null), hash: location.hash },
    { replace: true, state: location.state },
  );
}

/** The open signal and the two ways to change it, for the current route. */
export function useSignalParam() {
  const location = useLocation();
  const navigate = useNavigate();
  return {
    signalId: parseSignalParam(location.search),
    openSignal: (id: number) => openSignalAt(navigate, location, id),
    closeSignal: () => closeSignalAt(navigate, location),
  };
}
