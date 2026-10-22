import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "frogeye-theme";
const DEFAULT_THEME: Theme = "light";

const listeners = new Set<() => void>();

function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function persist(theme: Theme) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage can be unavailable (private mode, blocked site data); the theme still applies.
  }
}

/** The `dark` class on <html> is the single source of truth, so every reader agrees. */
function currentTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function paint(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
}

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function setTheme(theme: Theme) {
  persist(theme);
  const apply = () => {
    paint(theme);
    emit();
  };
  // A short crossfade between themes when the browser supports it and motion is welcome.
  if (typeof document.startViewTransition === "function" && !prefersReducedMotion()) {
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

/** Apply the stored theme at boot and follow changes made in other tabs. */
export function initTheme() {
  paint(readStoredTheme() ?? DEFAULT_THEME);
  window.addEventListener("storage", (event) => {
    if (event.key !== THEME_STORAGE_KEY) return;
    paint(readStoredTheme() ?? DEFAULT_THEME);
    emit();
  });
}

export function useTheme(): { theme: Theme; toggle(): void } {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => DEFAULT_THEME);
  return { theme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") };
}
