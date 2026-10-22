import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * The one ink-ruled section head: `body` sans semibold on a 1px ink rule (the
 * Briefing's section grammar), with quiet meta and optional actions on the right. Structure from
 * rules, not cards. The agent-run pages, the competitor list and the products list use it; a
 * section whose visible title is its chart's (the landscape) keeps the rule and names itself to
 * screen readers only (`srOnly`). `children` follow the title (a ledger's column words).
 */
export function SectionHead({
  id,
  title,
  meta,
  actions,
  srOnly = false,
  className,
  children,
}: {
  id: string;
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  /** The title is for screen readers: the section's visible title is its chart's. */
  srOnly?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-foreground pt-3 pb-4", className)}>
      <div className={cn("flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1", srOnly && "sr-only")}>
        <h2 id={id} className="text-body font-semibold">
          {title}
        </h2>
        {meta ? <span className="text-meta text-muted-foreground">{meta}</span> : null}
      </div>
      {children}
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
