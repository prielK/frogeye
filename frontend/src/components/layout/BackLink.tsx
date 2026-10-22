import { ArrowLeftIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

/** A muted way back above a page's title: `meta`, an arrow, underlined on hover, the app's focus ring. */
export const BACK_LINK =
  "mb-6 inline-flex w-fit items-center gap-1.5 rounded-xs text-meta text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none";

/** The way back to a list from its pages ("All competitors", "All products", "All signals"). */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className={BACK_LINK}>
      <ArrowLeftIcon aria-hidden className="size-3.5" />
      {children}
    </Link>
  );
}
