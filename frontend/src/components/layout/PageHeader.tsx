import type { ReactNode } from "react";

/** Page title in the headline serif, a one-line purpose, and optional actions. Sets the tab title. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-6">
      <title>{`${title} · FrogEye`}</title>
      <div className="flex min-w-0 flex-col gap-1.5">
        <h1 className="font-headline text-display">{title}</h1>
        {description ? <p className="max-w-[65ch] text-body text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
