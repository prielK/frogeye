import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatAbsolute, formatRelative, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";

type RelativeTimeProps = {
  iso: string | null | undefined;
  /** Read to assistive tech in place of the dash when there is no time. */
  emptyLabel?: string;
  className?: string;
};

/** "3h ago", with the exact local time one hover away. Renders "—" when there is no time. */
export function RelativeTime({ iso, emptyLabel = "Not yet", className }: RelativeTimeProps) {
  const now = useNow();
  if (!iso) {
    return (
      <span className={cn("text-muted-foreground", className)}>
        <span aria-hidden>—</span>
        <span className="sr-only">{emptyLabel}</span>
      </span>
    );
  }
  const absolute = formatAbsolute(iso);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time dateTime={iso} className={cn("whitespace-nowrap", className)}>
          {formatRelative(iso, now)}
        </time>
      </TooltipTrigger>
      <TooltipContent>{absolute}</TooltipContent>
    </Tooltip>
  );
}
