import { useQuery } from "@tanstack/react-query";
import { PanelLeftIcon, SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Sidebar } from "@/components/layout/Sidebar";
import { PersonaMenu } from "@/components/layout/PersonaMenu";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { Wordmark } from "@/components/layout/Wordmark";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { demoSnapshotQuery } from "@/lib/api/queries";
import { RUN_STAGES, useEvents, type LiveRun } from "@/lib/events";
import { formatCount } from "@/lib/format";
import { formatCalendarDay, formatClock, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";

/** A run that has sent no progress for this long has most likely stopped; the pill stands down. */
const LIVE_RUN_STALE_MS = 20 * 60_000;

/**
 * While a daily run is going (from its SSE progress, or seeded from the latest agent run on
 * load): what it is doing now, linking to the run's live step trace.
 */
function LiveRunPill({ run }: { run: LiveRun | null }) {
  const now = useNow(30_000);
  if (!run || now - run.updatedAt > LIVE_RUN_STALE_MS) return null;
  const { fetched, relevant, signals_new: fresh } = run.counts;
  const counts = [
    fetched ? `${formatCount(fetched)} fetched` : null,
    relevant ? `${formatCount(relevant)} relevant` : null,
    fresh ? `${formatCount(fresh)} new ${fresh === 1 ? "signal" : "signals"}` : null,
  ].filter(Boolean);
  const where =
    run.step > 0
      ? `Step ${Math.min(run.step, RUN_STAGES.length)} of ${RUN_STAGES.length}: ${run.activity.toLowerCase()}`
      : `In progress${run.startedAt ? ` since ${formatClock(Date.parse(run.startedAt))}` : ""}; its current step shows with the next update`;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          to={run.agentRunId ? `/admin/agents/${run.agentRunId}` : "/admin/runs"}
          aria-label={`Daily run in progress${run.step > 0 ? `: ${run.activity}` : ""}. Open the run`}
          className={cn(
            "inline-flex h-7 items-center gap-2 rounded-full border bg-card pr-3 pl-2.5 text-meta font-medium text-foreground",
            "animate-in duration-200 ease-out-expo fade-in-0",
            "transition-colors duration-150 hover:border-input-hover hover:bg-accent",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
          )}
        >
          <span aria-hidden className="relative flex size-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-primary opacity-60 [animation-duration:1.6s]" />
            <span className="relative size-2 rounded-full bg-primary" />
          </span>
          <span>Daily run</span>
          <span className="hidden text-muted-foreground lg:inline">{run.activity}</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        {where}
        {counts.length ? `. So far ${counts.join(", ")}.` : "."}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * On a database that started from the bundled demo snapshot: one quiet line saying the data
 * is a capture and when it was taken, so its dates read as that day's. The rest of the
 * sentence joins it on wide screens and is always in the tooltip.
 */
function DemoDataNote() {
  const snapshot = useQuery(demoSnapshotQuery());
  const capturedAt = snapshot.data?.captured_at;
  if (!capturedAt) return null;
  const day = formatCalendarDay(capturedAt);
  const more = "new data arrives with the daily run (needs an OpenAI key)";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <p
          tabIndex={0}
          className="hidden min-w-0 truncate rounded-sm text-meta text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none xl:block"
        >
          <span className="font-medium text-foreground">Demo data</span>
          {` captured ${day}`}
          <span className="hidden 2xl:inline">{` · ${more}`}</span>
        </p>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        {`This workspace started from a demo snapshot captured ${day}. New data arrives with the daily run, which needs an OpenAI key.`}
      </TooltipContent>
    </Tooltip>
  );
}

const isApple = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

/** Where the top bar's search goes: Search everything, its box focused (SignalFilters). */
const SEARCH_EVERYTHING = "/signals?mode=everything";
const FOCUS_SEARCH = { focusSearch: true };

/** Search everything, one click or Ctrl/⌘K away from any page. */
function SearchLink({ className }: { className?: string }) {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey) {
        event.preventDefault();
        void navigate(SEARCH_EVERYTHING, { state: FOCUS_SEARCH });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
  return (
    <Button asChild variant="outline" className={cn("h-8 justify-start gap-2 px-2.5 font-normal text-muted-foreground hover:text-foreground", className)}>
      <Link to={SEARCH_EVERYTHING} state={FOCUS_SEARCH} aria-label="Search everything: articles, signals and JFrog docs">
        <SearchIcon aria-hidden />
        <span>Search</span>
        <kbd aria-hidden className="ml-auto rounded-sm border bg-muted px-1.5 font-sans text-meta text-muted-foreground">
          {isApple ? "⌘K" : "Ctrl K"}
        </kbd>
      </Link>
    </Button>
  );
}

function MobileNav() {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="-ml-1 text-muted-foreground md:hidden" aria-label="Open navigation">
          <PanelLeftIcon />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-72 gap-0 bg-sidebar p-0">
        <div className="flex h-14 items-center border-b border-sidebar-border px-4">
          <SheetTitle asChild>
            <span>
              <Wordmark />
            </span>
          </SheetTitle>
          <SheetDescription className="sr-only">Main navigation</SheetDescription>
        </div>
        <Sidebar onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}

export function TopBar() {
  // The app's one live-events connection lives with the shell's top bar, which every signed-in page shows.
  const { liveRun } = useEvents();
  return (
    <header className="sticky top-0 z-(--z-sticky) flex h-14 border-b bg-background">
      <div className="flex shrink-0 items-center gap-1.5 px-4 md:w-60 md:border-r md:border-sidebar-border md:bg-sidebar">
        <MobileNav />
        <Link
          to="/briefing"
          aria-label="FrogEye, go to the Briefing"
          className="rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar focus-visible:outline-none"
        >
          <Wordmark />
        </Link>
      </div>
      <div className="flex min-w-0 flex-1 items-center gap-3 px-4 md:px-6 lg:px-10">
        <SearchLink className="hidden w-full max-w-xs sm:flex" />
        <DemoDataNote />
        <div className="ml-auto flex items-center gap-1.5">
          <LiveRunPill run={liveRun} />
          <ThemeToggle />
          <Separator orientation="vertical" className="mx-1 h-5!" />
          <PersonaMenu />
        </div>
      </div>
    </header>
  );
}
