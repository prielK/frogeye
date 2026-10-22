import type { ReactNode } from "react";

/*
 * A key figure, never a stat tile (shared by both deep dives): a `meta` label
 * over its value and the `meta` lines that say where it comes from and when.
 */

/** A key figure's value, and its lines of context and source. */
export const VALUE = "text-body font-semibold text-foreground";
export const SUB = "text-meta text-pretty text-muted-foreground";

/** One key figure: a `meta` label over its value and the lines saying where it comes from. */
export function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <li className="flex min-w-0 flex-col gap-1 border-t pt-3">
      <span data-kpi-label className="text-meta text-muted-foreground">
        {label}
      </span>
      {children}
    </li>
  );
}
