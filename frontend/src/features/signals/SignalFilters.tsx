import { SlidersHorizontalIcon, XIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { CompetitorSelectItems } from "@/components/common/CompetitorSelectItems";
import { SearchInput } from "@/components/common/SearchInput";
import { useDebouncedSearch } from "@/components/common/useDebouncedSearch";
import { STANCE, trustFor } from "@/components/signal/meta";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { Competitor } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import {
  IMPORTANCE_STEPS,
  KINDS,
  SCOPES,
  SIGNAL_TYPES,
  SINCE,
  STANCES,
  STATUSES,
  hasFilters,
  secondaryCount,
  useCapabilities,
  useKits,
  type ExplorerFilters,
} from "./useSignals";

const ALL = "all";

type FacetProps = {
  /** The accessible name, e.g. "Competitor". */
  label: string;
  value: string | number | undefined;
  /** The "no filter" choice, e.g. "All companies". */
  any: string;
  onChange: (value: string | undefined) => void;
  /** Width from `sm` up; below it facets share the row. */
  width: string;
  disabled?: boolean;
  children: ReactNode;
};

/** One facet as a select. Unset facets read muted, so the ones narrowing the list stand out. */
function Facet({ label, value, any, onChange, width, disabled, children }: FacetProps) {
  const set = value !== undefined;
  return (
    <Select value={set ? String(value) : ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger
        aria-label={label}
        disabled={disabled}
        className={cn("min-w-0 flex-1 basis-40 sm:flex-none", width, !set && "text-muted-foreground")}
      >
        <SelectValue placeholder="Loading…" />
      </SelectTrigger>
      <SelectContent position="popper" className="max-h-80">
        <SelectItem value={ALL}>{any}</SelectItem>
        {children}
      </SelectContent>
    </Select>
  );
}

type SignalFiltersProps = {
  filters: ExplorerFilters;
  /** Analysts also filter by status. */
  analyst: boolean;
  competitors: Competitor[] | undefined;
  onChange: (next: ExplorerFilters) => void;
};

/**
 * Above the list it scopes: the search box (debounced) and its "Search everything" mode, then
 * the facets readers reach for most; scope, capability, KIT and (for analysts) status wait
 * behind "More filters" until used. Search everything narrows by company, date and kind only.
 */
export function SignalFilters({ filters, analyst, competitors, onChange }: SignalFiltersProps) {
  const [draft, setDraft] = useDebouncedSearch(filters.q ?? "", (q) => onChange({ ...filters, q: q || undefined }));
  const hidden = secondaryCount(filters);
  const [more, setMore] = useState(hidden > 0);
  const switchId = useId();
  const moreId = useId();
  const capabilities = useCapabilities();
  const kits = useKits();
  const everything = Boolean(filters.everything);
  // Arriving from the top bar's search: the box is ready to type in.
  const location = useLocation();
  const box = useRef<HTMLInputElement>(null);
  const focusSearch = Boolean((location.state as { focusSearch?: boolean } | null)?.focusSearch);
  useEffect(() => {
    if (focusSearch) box.current?.focus();
  }, [focusSearch, location.key]);

  const set = (patch: Partial<ExplorerFilters>) => onChange({ ...filters, ...patch });

  const competitor = (
    <Facet label="Competitor" value={filters.competitor} any="All companies" onChange={(v) => set({ competitor: v })} width="sm:w-44" disabled={!competitors}>
      <CompetitorSelectItems competitors={competitors} />
    </Facet>
  );
  const since = (
    <Facet label="Updated" value={filters.since} any="Any time" onChange={(v) => set({ since: v as ExplorerFilters["since"] })} width="sm:w-36">
      {SINCE.map((s) => (
        <SelectItem key={s.value} value={s.value}>
          {s.label}
        </SelectItem>
      ))}
    </Facet>
  );

  return (
    <div role="search" aria-label="Search and filter signals" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5">
        <SearchInput
          value={draft}
          onChange={setDraft}
          label={everything ? "Search articles, signals and JFrog docs" : "Search signals"}
          className="w-full sm:w-96"
          inputRef={box}
        />
        <div className="flex items-center gap-2">
          <Switch id={switchId} checked={everything} onCheckedChange={(on) => set({ everything: on || undefined })} />
          <label htmlFor={switchId} className="cursor-pointer text-body font-medium text-foreground select-none">
            Search everything
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {competitor}
        {everything ? (
          <>
            {since}
            <Facet label="Result kind" value={filters.kind} any="All kinds" onChange={(v) => set({ kind: v as ExplorerFilters["kind"] })} width="sm:w-36">
              {KINDS.map((k) => (
                <SelectItem key={k.value} value={k.value}>
                  {k.plural}
                </SelectItem>
              ))}
            </Facet>
          </>
        ) : (
          <>
            <Facet label="Signal type" value={filters.type} any="All types" onChange={(v) => set({ type: v as ExplorerFilters["type"] })} width="sm:w-44">
              {SIGNAL_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </Facet>
            <Facet label="Stance" value={filters.stance} any="Any stance" onChange={(v) => set({ stance: v as ExplorerFilters["stance"] })} width="sm:w-36">
              {STANCES.map((s) => {
                // Icon + word, as everywhere a stance shows; the icon carries its own colour class
                // because the select mutes any icon without one.
                const look = STANCE[s]!;
                const Icon = look.icon;
                return (
                  <SelectItem key={s} value={s}>
                    <Icon aria-hidden className={cn("size-3.5", look.className)} />
                    <span className={look.className}>{look.label}</span>
                  </SelectItem>
                );
              })}
            </Facet>
            <Facet
              label="Minimum importance"
              value={filters.min_importance}
              any="Any importance"
              onChange={(v) => set({ min_importance: v ? (Number(v) as ExplorerFilters["min_importance"]) : undefined })}
              width="sm:w-40"
            >
              {IMPORTANCE_STEPS.map((step) => (
                <SelectItem key={step} value={String(step)}>
                  Importance {step}+
                </SelectItem>
              ))}
            </Facet>
            {since}
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={more}
              aria-controls={moreId}
              onClick={() => setMore((open) => !open)}
              className={cn("text-muted-foreground hover:text-foreground", more && "bg-accent text-foreground")}
            >
              <SlidersHorizontalIcon />
              More filters
              {!more && hidden > 0 ? <span className="font-semibold text-foreground">· {hidden}</span> : null}
            </Button>
          </>
        )}
        {hasFilters(filters) ? (
          <Button variant="ghost" size="sm" onClick={() => onChange({ everything: filters.everything })} className="text-muted-foreground hover:text-foreground">
            <XIcon />
            Clear filters
          </Button>
        ) : null}
      </div>

      {!everything && more ? (
        <div id={moreId} className="flex flex-wrap items-center gap-2">
          <Facet label="Scope" value={filters.scope} any="Any scope" onChange={(v) => set({ scope: v as ExplorerFilters["scope"] })} width="sm:w-36">
            {SCOPES.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </Facet>
          <Facet
            label="Capability"
            value={filters.capability}
            any="Any capability"
            onChange={(v) => set({ capability: v })}
            width="sm:w-52"
            disabled={!capabilities.data}
          >
            {(capabilities.data ?? []).map((category) => (
              <SelectGroup key={category.slug}>
                <SelectSeparator />
                <SelectLabel>{category.name}</SelectLabel>
                {category.capabilities.map((c) => (
                  <SelectItem key={c.slug} value={c.slug}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
          </Facet>
          <Facet
            label="Key intelligence topic"
            value={filters.kit}
            any="Any key topic"
            onChange={(v) => set({ kit: v })}
            width="sm:w-56"
            disabled={!kits.data}
          >
            {(kits.data ?? []).map((k) => (
              <SelectItem key={k.slug} value={k.slug}>
                {k.title}
              </SelectItem>
            ))}
          </Facet>
          {analyst ? (
            <Facet
              label="Status"
              value={filters.status}
              any="Published or in review"
              onChange={(v) => set({ status: v as ExplorerFilters["status"] })}
              width="sm:w-52"
            >
              {STATUSES.map((s) => {
                const trust = trustFor(s.value);
                const Icon = trust.icon;
                return (
                  <SelectItem key={s.value} value={s.value}>
                    <Icon aria-hidden className={cn("size-3.5", trust.className)} />
                    {s.label}
                  </SelectItem>
                );
              })}
            </Facet>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
