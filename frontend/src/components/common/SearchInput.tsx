import { SearchIcon, XIcon } from "lucide-react";
import type { Ref } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type SearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  /** Accessible name; also the placeholder unless one is given. */
  label: string;
  placeholder?: string;
  className?: string;
  /** The input itself, e.g. to focus it. */
  inputRef?: Ref<HTMLInputElement>;
};

/** A search field with our own clear button (Escape clears too) in place of the browser's glyph. */
export function SearchInput({ value, onChange, label, placeholder = label, className, inputRef }: SearchInputProps) {
  return (
    <div className={cn("relative", className)}>
      <SearchIcon aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.preventDefault();
            onChange("");
          }
        }}
        className="pr-8 pl-8 [&::-webkit-search-cancel-button]:appearance-none"
      />
      {value ? (
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute top-1/2 right-1.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <XIcon />
        </Button>
      ) : null}
    </div>
  );
}
