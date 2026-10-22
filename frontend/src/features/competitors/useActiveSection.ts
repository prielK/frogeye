import { useCallback, useEffect, useRef, useState } from "react";

/*
 * Which section of a long page is being read (the section menu's highlight): the last section
 * whose top has passed a reading line just under the sticky bars, or the last one once the page
 * is scrolled to its end (a short closing section never reaches the line). Scroll reads are
 * batched to one per frame. A jump from the menu marks its target at once and holds it while the
 * smooth scroll passes the sections in between: for a moment (a timer, never a clock reading), or
 * until the reader takes over (a wheel, a touch or a key).
 */

/** The reading line, in px from the viewport's top: below the 56px top bar and the phone's jump bar. */
const READING_LINE = 150;
const HOLD_MS = 900;

export function useActiveSection<T extends string>(ids: readonly T[]) {
  const [active, setActive] = useState<T>(ids[0]!);
  const held = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    const frame = window.requestAnimationFrame?.bind(window) ?? ((cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16));
    const cancel = window.cancelAnimationFrame?.bind(window) ?? window.clearTimeout.bind(window);
    let pending = 0;
    const read = () => {
      pending = 0;
      if (held.current) return;
      const atEnd = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      let current = ids[0]!;
      if (atEnd) current = ids.at(-1)!;
      else
        for (const id of ids) {
          const top = document.getElementById(id)?.getBoundingClientRect().top;
          if (top !== undefined && top <= READING_LINE) current = id;
        }
      setActive((was) => (was === current ? was : current));
    };
    const schedule = () => {
      if (!pending) pending = frame(read);
    };
    // The reader scrolling by hand ends a jump's hold at once. A key pressed in the jump select
    // (choosing a section with Enter) is the jump itself, not a scroll by hand.
    const release = (event: Event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.type === "keydown" && target?.closest('[role="listbox"], [role="combobox"], [data-jump]')) return;
      held.current = false;
      window.clearTimeout(timer.current);
    };
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    for (const type of ["wheel", "touchstart", "keydown"] as const) window.addEventListener(type, release, { passive: true });
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      for (const type of ["wheel", "touchstart", "keydown"] as const) window.removeEventListener(type, release);
      window.clearTimeout(timer.current);
      if (pending) cancel(pending);
    };
  }, [ids]);

  /** Mark `id` as the section in view now (a jump), ahead of the scroll that brings it there. */
  const mark = useCallback((id: T) => {
    held.current = true;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => (held.current = false), HOLD_MS);
    setActive(id);
  }, []);

  return { active, mark };
}
