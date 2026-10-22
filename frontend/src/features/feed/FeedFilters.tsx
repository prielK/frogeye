import { XIcon } from "lucide-react";
import { CompetitorSelectItems } from "@/components/common/CompetitorSelectItems";
import { SearchInput } from "@/components/common/SearchInput";
import { useDebouncedSearch } from "@/components/common/useDebouncedSearch";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Competitor } from "@/lib/api/client";
import { SCOPES, STATUSES, hasFilters, type DocumentFilters } from "./useDocuments";

const ALL = "all";

type FeedFiltersProps = {
  filters: DocumentFilters;
  competitors: Competitor[] | undefined;
  onChange: (next: DocumentFilters) => void;
};

const SELECT_WIDTH = "min-w-0 flex-1 basis-40 sm:flex-none";

/** One row above the list it scopes: title search (debounced), competitor, scope and status. */
export function FeedFilters({ filters, competitors, onChange }: FeedFiltersProps) {
  // Debounced, and following the URL when Clear filters or back/forward change the term.
  const [draft, setDraft] = useDebouncedSearch(filters.q ?? "", (q) => onChange({ ...filters, q: q || undefined }));

  const set = (patch: Partial<DocumentFilters>) => onChange({ ...filters, ...patch });

  return (
    <div role="search" aria-label="Filter the feed" className="flex flex-wrap items-center gap-2">
      <SearchInput value={draft} onChange={setDraft} label="Search titles" className="w-full sm:w-72" />

      <Select value={filters.competitor ?? ALL} onValueChange={(v) => set({ competitor: v === ALL ? undefined : v })}>
        <SelectTrigger aria-label="Competitor" className={`${SELECT_WIDTH} sm:w-48`} disabled={!competitors}>
          <SelectValue placeholder="Loading competitors…" />
        </SelectTrigger>
        <SelectContent position="popper" className="max-h-80">
          <SelectItem value={ALL}>All companies</SelectItem>
          <CompetitorSelectItems competitors={competitors} />
        </SelectContent>
      </Select>

      <Select value={filters.scope ?? ALL} onValueChange={(v) => set({ scope: v === ALL ? undefined : (v as DocumentFilters["scope"]) })}>
        <SelectTrigger aria-label="Scope" className={`${SELECT_WIDTH} sm:w-36`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper">
          <SelectItem value={ALL}>Any scope</SelectItem>
          {SCOPES.map((s) => (
            <SelectItem key={s.value} value={s.value}>
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={filters.status ?? ALL} onValueChange={(v) => set({ status: v === ALL ? undefined : (v as DocumentFilters["status"]) })}>
        <SelectTrigger aria-label="Status" className={`${SELECT_WIDTH} sm:w-44`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper">
          <SelectItem value={ALL}>All but duplicates</SelectItem>
          {STATUSES.map((s) => (
            <SelectItem key={s.value} value={s.value}>
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {hasFilters(filters) ? (
        <Button variant="ghost" size="sm" onClick={() => onChange({})} className="text-muted-foreground hover:text-foreground">
          <XIcon />
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}
