import { ArrowUpRightIcon, CircleAlertIcon, CircleCheckIcon, CircleXIcon, ClockIcon, RotateCwIcon } from "lucide-react";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type SourceOut, type SourceTestOut } from "@/lib/api/client";
import { formatCount, hostOf, isWebUrl, plural } from "@/lib/format";
import { kindLabel } from "./useSources";

type TestState = {
  pending: boolean;
  result: SourceTestOut | undefined;
  error: unknown;
};

type TestFetchDialogProps = {
  source: SourceOut | null;
  state: TestState;
  onRetry: () => void;
  onOpenChange: (open: boolean) => void;
};

function Pending({ source }: { source: SourceOut }) {
  return (
    <div role="status" aria-label={`Fetching ${source.name}`} className="flex flex-col gap-3">
      <p className="text-body text-muted-foreground">
        Fetching from {hostOf(source.url)}… Rate-limited sources such as GDELT and SEC can take up to a minute.
      </p>
      <ul className="divide-y rounded-md border">
        {Array.from({ length: 3 }, (_, i) => (
          <li key={i} className="flex flex-col gap-1.5 px-3 py-2.5">
            <Skeleton className={i === 1 ? "h-4 w-3/5" : "h-4 w-4/5"} />
            <Skeleton className="h-3 w-16" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Failure({ title, detail, hint }: { title: string; detail?: string | null; hint: string }) {
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-md bg-threat-soft px-3.5 py-3 text-threat">
      <p className="flex items-center gap-2 text-body font-medium">
        <CircleXIcon aria-hidden className="size-4 shrink-0" />
        {title}
      </p>
      {detail ? (
        <pre className="max-h-32 overflow-auto font-mono text-meta leading-relaxed break-words whitespace-pre-wrap text-foreground">
          {detail}
        </pre>
      ) : null}
      <p className="text-meta text-foreground/80">{hint}</p>
    </div>
  );
}

/** The first items the fetch brought back, each opening its page; `emptyDate` names a missing date in the source's terms (null: a watched page, which has no date to give). */
function Samples({ sample, emptyDate }: { sample: SourceTestOut["sample"]; emptyDate: string | null }) {
  return (
    <ol className="divide-y rounded-md border">
      {sample.map((item) => (
        <li key={item.url} className="flex flex-col gap-0.5 px-3 py-2.5">
          {isWebUrl(item.url) ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group/sample rounded-xs text-body font-medium text-foreground underline-offset-[0.2em] hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {item.title?.trim() || hostOf(item.url)}
              <ArrowUpRightIcon aria-hidden className="ml-0.5 inline size-3.5 text-muted-foreground opacity-0 group-hover/sample:opacity-100" />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            <span className="text-body font-medium text-foreground">{item.title?.trim() || hostOf(item.url)}</span>
          )}
          <span className="text-meta text-muted-foreground">
            {hostOf(item.url)}
            {emptyDate === null ? null : (
              <>
                {" · "}
                <RelativeTime iso={item.published_at} emptyLabel={emptyDate} />
              </>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** A plain outcome line: a check, the count in medium weight, then what follows in muted text. */
function Outcome({ lead, rest }: { lead: string; rest?: string }) {
  return (
    <p className="flex items-start gap-2 text-body text-foreground">
      <CircleCheckIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-primary-ink" />
      <span>
        <span className="font-medium">{lead}</span>
        {rest ? <span className="text-muted-foreground">{` ${rest}`}</span> : null}
      </span>
    </p>
  );
}

/** A result that worked but needs a word of caution (a partial board, a page with no text). */
function Caution({ children }: { children: string }) {
  return (
    <p className="flex items-start gap-2 rounded-md bg-warning-soft px-3.5 py-3 text-body text-pretty text-foreground">
      <CircleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
      {children}
    </p>
  );
}

const firstOf = (sample: SourceTestOut["sample"], total: number) => (total > sample.length ? (sample.length === 1 ? "The first is below." : `The first ${sample.length} are below.`) : undefined);

/**
 * A job board's dry run: its postings, never "items", and whether the listing
 * was the whole board: a partial one is stored but measures nothing (open roles count complete
 * listings only), and a complete one with no posting is a measured none.
 */
function BoardResult({ result, postings }: { result: SourceTestOut; postings: number }) {
  if (result.postings_complete && postings === 0) return <Outcome lead="The board is listed in full and has no open posting right now." />;
  const count = `Listed ${plural(postings, "posting")}`;
  return (
    <div className="flex flex-col gap-3">
      {result.postings_complete ? (
        <Outcome lead={`${count}, the whole board.`} rest={firstOf(result.sample, postings)} />
      ) : (
        <Caution>{`${count}, only part of the board. A partial listing is stored but measures nothing: open roles and their changes count complete listings only.`}</Caution>
      )}
      {result.sample.length > 0 ? <Samples sample={result.sample} emptyDate="No posting date" /> : null}
    </div>
  );
}

/** A watched page's dry run: how much main text was read, the page itself below. */
function PageResult({ result, chars }: { result: SourceTestOut; chars: number }) {
  if (chars === 0 || result.item_count === 0) {
    return <Caution>The page answered, but FrogEye read no main text from it. It may need JavaScript to show its text, or its layout changed; the next run tries again.</Caution>;
  }
  return (
    <div className="flex flex-col gap-3">
      <Outcome lead={`Read the page: ${formatCount(chars)} ${chars === 1 ? "character" : "characters"} of main text.`} />
      {result.sample.length > 0 ? <Samples sample={result.sample} emptyDate={null} /> : null}
    </div>
  );
}

/** A job board or watched page is read at most once per local day, by any path. */
const ONCE_A_DAY = new Set(["job_board", "page_watch"]);

/** A board or page already read today: the API's words (409), calmly, with when it is read next. */
function AlreadyRead({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-md bg-secondary px-3.5 py-3">
      <p className="flex items-center gap-2 text-body font-medium text-foreground">
        <ClockIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        {detail}
      </p>
      <p className="text-meta text-pretty text-muted-foreground">FrogEye reads each job board and watched page at most once a day, whatever asks: the daily run, a manual run or a test.</p>
    </div>
  );
}

function Result({ result }: { result: SourceTestOut }) {
  if (!result.ok) {
    return (
      <Failure
        title="The fetch failed."
        detail={result.error}
        hint="Check that the URL still works in a browser and isn't blocking automated readers. The next run retries it either way."
      />
    );
  }
  // The API counts in the source's own unit (SourceTestOut): a board's postings, a page's text.
  if (typeof result.postings_count === "number") return <BoardResult result={result} postings={result.postings_count} />;
  if (typeof result.page_text_chars === "number") return <PageResult result={result} chars={result.page_text_chars} />;
  // a repository's stars and forks are readings, never "0 items".
  if (typeof result.metrics_count === "number") {
    return <Outcome lead={`Read ${plural(result.metrics_count, "reading")}.`} rest="Stars and forks are stored as one daily count each, not as items." />;
  }
  if (result.item_count === 0) {
    return (
      <p className="flex items-start gap-2 rounded-md bg-warning-soft px-3.5 py-3 text-body text-foreground">
        <CircleCheckIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
        The source answered but returned no items. A quiet feed is normal; an empty one for weeks usually means the URL moved.
      </p>
    );
  }
  const more = result.item_count - result.sample.length;
  return (
    <div className="flex flex-col gap-3">
      <Outcome lead={`Fetched ${plural(result.item_count, "item")}.`} rest={more > 0 ? (result.sample.length === 1 ? "The newest is below." : `The newest ${result.sample.length} are below.`) : undefined} />
      <Samples sample={result.sample} emptyDate="No publication date" />
    </div>
  );
}

/** A dry run of one source's collector, with the first items it would bring in. */
export function TestFetchDialog({ source, state, onRetry, onOpenChange }: TestFetchDialogProps) {
  return (
    <Dialog open={source !== null} onOpenChange={onOpenChange}>
      {source ? (
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Test {source.name}</DialogTitle>
            <DialogDescription>
              A dry run of the {kindLabel(source.kind)} collector. Nothing is saved and the source's health doesn't change.
              {ONCE_A_DAY.has(source.kind) ? " It is today's one request to it: the day's run then leaves it for tomorrow." : null}
            </DialogDescription>
            {isWebUrl(source.url) ? (
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="w-fit max-w-full truncate rounded-xs font-mono text-meta text-muted-foreground underline-offset-[0.2em] hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {source.url}
              </a>
            ) : (
              <span className="w-fit max-w-full truncate font-mono text-meta text-muted-foreground">{source.url}</span>
            )}
          </DialogHeader>

          <div aria-live="polite" className="min-h-28">
            {state.pending ? (
              <Pending source={source} />
            ) : state.error instanceof ApiError && state.error.status === 409 ? (
              <AlreadyRead detail={state.error.message.startsWith("Request failed") ? "Already fetched today; next fetch tomorrow" : state.error.message} />
            ) : state.error ? (
              <Failure
                title="The test couldn't run."
                hint={
                  state.error instanceof ApiError && state.error.status === 403
                    ? "Only CI analysts can test sources."
                    : "FrogEye's API didn't answer. Check your connection, then test again."
                }
              />
            ) : state.result ? (
              <Result result={state.result} />
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={onRetry} disabled={state.pending}>
              <RotateCwIcon />
              Test again
            </Button>
            <DialogClose asChild>
              <Button variant="secondary">Done</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
