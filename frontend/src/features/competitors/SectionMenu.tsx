import { linkClick } from "@/components/charts/scales";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { SECTIONS, type SectionId } from "./useCompetitorProfile";

/*
 * The deep dive's section menu (layout A). From `xl` it is a sticky rail beside the page: the
 * exact section labels, the section being read marked as the app's navigation marks its active
 * item (a tonal `accent` fill, `foreground` medium text) and by `aria-current`, the sections with nothing of their own yet marked by the kit's dashed ring
 * (the "not known" idiom) and said to screen readers. Below `xl` it collapses into one sticky
 * select under the top bar, naming the section in view. Both jump to the section and keep it in
 * the address (`#pricing`), so a link can open the page at a section.
 */

type MenuProps = {
  active: SectionId;
  /** Which sections have data of their own. */
  present: Record<SectionId, boolean>;
  onJump: (id: SectionId) => void;
};

/** The rail (from `xl`). */
export function SectionRail({ name, active, present, onJump }: MenuProps & { name: string }) {
  const count = SECTIONS.filter((s) => present[s.id]).length;
  // A plain click jumps in place; a modified click (new tab) keeps the browser's behaviour.
  const go = (id: SectionId) => linkClick(`#${id}`, () => onJump(id));
  return (
    <nav aria-label="Sections" className="flex flex-col gap-4">
      <p className="truncate text-body font-semibold text-foreground" aria-hidden>
        {name}
      </p>
      <ol className="-mx-2.5 flex flex-col gap-px">
        {SECTIONS.map((s) => {
          const current = active === s.id;
          return (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                onClick={go(s.id)}
                aria-current={current ? "location" : undefined}
                aria-label={present[s.id] ? undefined : `${s.label}, no data yet`}
                className={cn(
                  "flex h-8 items-center gap-2 rounded-md px-2.5 text-body text-muted-foreground",
                  "transition-[color,background-color] duration-150 ease-out-quart hover:bg-accent/60 hover:text-foreground",
                  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  current && "bg-accent font-medium text-foreground hover:bg-accent",
                )}
              >
                <span className="min-w-0 truncate">{s.label}</span>
                {present[s.id] ? null : (
                  <span aria-hidden className="ml-auto size-2 shrink-0 rounded-full border border-dashed border-muted-foreground" />
                )}
              </a>
            </li>
          );
        })}
      </ol>
      <p className="flex items-center gap-1.5 text-meta text-muted-foreground">
        {count === SECTIONS.length ? (
          `All ${SECTIONS.length} sections have data`
        ) : (
          <>
            <span aria-hidden className="size-2 shrink-0 rounded-full border border-dashed border-muted-foreground" />
            {`${count} of ${SECTIONS.length} sections have data`}
          </>
        )}
      </p>
    </nav>
  );
}

/** The phone and tablet form (below `xl`): a sticky bar under the top bar with one select; a section with no data yet wears the rail's dashed ring. */
export function SectionJump({ name, active, present, onJump }: MenuProps & { name: string }) {
  return (
    <div
      data-jump
      className={cn(
        "sticky top-14 z-(--z-sticky) -mx-4 mb-8 flex items-center gap-4 border-b bg-background px-4 py-2.5",
        "md:-mx-6 md:px-6 lg:-mx-10 lg:px-10 xl:hidden",
      )}
    >
      <span aria-hidden className="hidden min-w-0 truncate text-body font-semibold text-foreground sm:block">
        {name}
      </span>
      {/* The jump runs once the list has closed: while it is open Radix traps focus inside it, so a
          heading focused then would lose focus again. */}
      <Select value={active} onValueChange={(v) => window.setTimeout(() => onJump(v as SectionId), 0)}>
        <SelectTrigger aria-label="Jump to section" className="w-full min-w-0 sm:ml-auto sm:w-64">
          <SelectValue />
        </SelectTrigger>
        {/* Radix returns focus to the trigger on close; the jump has moved it to the section's head, so keep it there. */}
        <SelectContent position="popper" className="max-h-96" onCloseAutoFocus={(event) => event.preventDefault()}>
          {SECTIONS.map((s) => (
            <SelectItem key={s.id} value={s.id}>
              <span className="flex w-full items-center gap-2">
                {s.label}
                {present[s.id] ? null : (
                  <>
                    <span className="sr-only">, no data yet</span>
                    <span aria-hidden className="size-2 shrink-0 rounded-full border border-dashed border-muted-foreground" />
                  </>
                )}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
