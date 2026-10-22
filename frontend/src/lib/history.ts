import { useContext } from "react";
import { UNSAFE_NavigationContext, useLocation } from "react-router";

/**
 * Whether the current history entry has an in-app entry before it, so a "Back" control can
 * step back without leaving FrogEye (or doing nothing). Location keys cannot tell: a signed-out
 * deep link goes /signals/101 → /login → /signals/101 by two replaces and gets a fresh key, yet
 * is still the tab's only entry. So read the router's own entry number: BrowserRouter keeps it
 * in `history.state.idx` (0 for the tab's first app entry; a replace keeps it), a memory router
 * (tests) as `navigator.index`.
 */
export function useHasInAppHistory(): boolean {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useLocation(); // re-read on every navigation
  const memoryIndex = (navigator as { index?: unknown }).index;
  if (typeof memoryIndex === "number") return memoryIndex > 0;
  const idx = (window.history.state as { idx?: unknown } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}
