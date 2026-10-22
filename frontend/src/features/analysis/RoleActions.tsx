import { ChevronDownIcon } from "lucide-react";
import { useId, useState } from "react";
import type { ActionGroup, ActionItem, Level, RoleActionSet } from "@/components/signal/actions";
import { plural } from "@/lib/format";
import { ROLES, type Edition } from "@/lib/roles";
import { cn } from "@/lib/utils";

/*
 * Recommended actions, one block per role. The reader's own role opens first and the others
 * wait behind a disclosure; master readers (CI analysts) see all four open. A role with nothing
 * to do is not shown. Groups ("Talk track", "Objections") are `meta` labels over `body` lists;
 * an objection is a two-part item, what the customer says and the answer.
 */

const RISK: Record<Level, string> = { high: "High", medium: "Medium", low: "Low" };

function Item({ item, group }: { item: ActionItem; group: ActionGroup }) {
  if (item.kind === "objection") {
    return (
      <li className="grid grid-cols-[4.75rem_minmax(0,1fr)] gap-x-3 gap-y-1 py-0.5">
        <span className="pt-0.5 text-meta font-medium text-muted-foreground">Objection</span>
        <p className="text-body text-foreground">“{item.objection}”</p>
        {item.response ? (
          <>
            <span className="pt-0.5 text-meta font-medium text-muted-foreground">Response</span>
            <p className="text-body text-foreground">{item.response}</p>
          </>
        ) : null}
      </li>
    );
  }
  // Talk-track lines are things to say out loud, so they read as speech.
  return <li className="text-body text-foreground">{group.key === "talk_track" ? `“${item.text}”` : item.text}</li>;
}

function Group({ group }: { group: ActionGroup }) {
  const id = useId();
  const objections = group.items.some((i) => i.kind === "objection");
  return (
    <div className="flex flex-col gap-1.5">
      {group.label ? (
        <h4 id={id} className="text-meta font-medium text-muted-foreground">
          {group.label}
        </h4>
      ) : null}
      <ul
        aria-labelledby={group.label ? id : undefined}
        className={cn(
          "flex flex-col",
          objections ? "gap-3" : "list-disc gap-1.5 pl-[1.125rem] marker:text-muted-foreground",
        )}
      >
        {group.items.map((item, i) => (
          <Item key={i} item={item} group={group} />
        ))}
      </ul>
    </div>
  );
}

function RoleBlock({ set, marker, initiallyOpen }: { set: RoleActionSet; marker: string | null; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const panel = useId();
  return (
    <div className="border-t first:border-t-0">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panel}
          onClick={() => setOpen((o) => !o)}
          className="group flex w-full items-center gap-3 rounded-sm py-3 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
            <span className="text-body font-semibold text-foreground decoration-1 underline-offset-[0.22em] group-hover:underline">
              {ROLES[set.role].label}
            </span>
            <span className="text-meta text-muted-foreground">
              {plural(set.count, "action")}
              {marker ? ` · ${marker}` : ""}
            </span>
          </span>
          <ChevronDownIcon
            aria-hidden
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform duration-200 ease-out-quart group-hover:text-foreground",
              open && "rotate-180",
            )}
          />
        </button>
      </h3>
      <div id={panel} hidden={!open} className="flex flex-col gap-4 pb-5">
        {set.risk ? (
          <p className="text-body text-muted-foreground">
            Risk level for JFrog: <span className="font-semibold text-foreground">{RISK[set.risk]}</span>
          </p>
        ) : null}
        {set.groups.map((group) => (
          <Group key={group.key} group={group} />
        ))}
      </div>
    </div>
  );
}

/**
 * The role blocks, the reader's role first (`edition`, editionFor(user.role)) and marked "your
 * role"; the master, an analyst's, opens every block.
 */
export function RoleActions({ sets, edition }: { sets: RoleActionSet[]; edition: Edition }) {
  const master = edition === "master";
  const ordered = master ? sets : [...sets.filter((s) => s.role === edition), ...sets.filter((s) => s.role !== edition)];
  const ownMissing = !master && !sets.some((s) => s.role === edition);
  return (
    <div className="flex flex-col">
      {ownMissing ? (
        <p className="pb-3 text-body text-muted-foreground">
          Nothing for {ROLES[edition].label} to act on in this story. Other teams' actions are below.
        </p>
      ) : null}
      {ordered.map((set) => (
        <RoleBlock key={set.role} set={set} marker={set.role === edition ? "your role" : null} initiallyOpen={master || set.role === edition} />
      ))}
    </div>
  );
}
