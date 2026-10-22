import { ArrowUpRightIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { INLINE_LINK } from "@/components/common/links";
import { Badge } from "@/components/ui/badge";
import { isWebUrl } from "@/lib/format";
import { formatCalendarDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { analysisHref } from "./param";
import { signalSourceId, sourceOutlet, type CitedSource } from "./query";

const LINK = INLINE_LINK;

/**
 * A link out of FrogEye: new tab, never an opener, an arrow on hover or focus. Anything but http(s)
 * stays text. `arrow={false}` leaves the arrow out on dense lines of names, where its reserved
 * space would read as a gap after every name; the new tab is still announced.
 */
export function ExternalLink({ href, children, className, arrow = true }: { href: string; children: ReactNode; className?: string; arrow?: boolean }) {
  if (!isWebUrl(href)) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cn("group/link", LINK, className)}>
      {children}
      {arrow ? (
        <ArrowUpRightIcon
          aria-hidden
          className="ml-0.5 inline size-3.5 -translate-y-px text-muted-foreground opacity-0 transition-opacity duration-150 group-hover/link:opacity-100 group-focus-visible/link:opacity-100"
        />
      ) : null}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/**
 * A numbered source's title: documents and JFrog docs link out; a cited signal opens its own
 * analysis page; a signal hidden from this reader ("Signal not
 * available") is plain text.
 */
export function SourceLink({ source, className }: { source: CitedSource; className?: string }) {
  const signalId = signalSourceId(source);
  if (signalId !== null) {
    return (
      <Link to={analysisHref(signalId)} className={cn(LINK, className)}>
        {source.title}
      </Link>
    );
  }
  if (source.url && source.kind !== "signal") {
    return (
      <ExternalLink href={source.url} className={className}>
        {source.title}
      </ExternalLink>
    );
  }
  return <span className={cn(className, "text-muted-foreground")}>{source.title}</span>;
}

/** The line under a source's title: outlet · publication day · "Primary". The page's Sources list and the quick look share it. */
export function SourceMeta({ source }: { source: CitedSource }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
      <span>{sourceOutlet(source)}</span>
      {source.published_at ? (
        <>
          <span aria-hidden>·</span>
          <time dateTime={source.published_at}>{formatCalendarDay(source.published_at)}</time>
        </>
      ) : null}
      {source.is_primary ? <Badge variant="outline">Primary</Badge> : null}
    </p>
  );
}
