import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * A count with tabular digits (so columns line up) but proportional separators: the text
 * face's tabular comma is a full figure wide and would read "5 , 219". `format` renders other
 * figures the same way (money "$0.62", compact counts "182K").
 */
export function Num({ value, format = formatCount, className }: { value: number; format?: (value: number) => string; className?: string }) {
  const parts = format(value).split(/(\D+)/);
  return (
    <span data-numeric className={cn("whitespace-nowrap", className)}>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <span key={i} className="[font-variant-numeric:lining-nums_proportional-nums]">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </span>
  );
}
