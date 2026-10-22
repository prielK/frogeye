import { EyeIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** The FrogEye nameplate: a green frog-eye glyph and the name set in the headline serif. */
export function Wordmark({ size = "default", className }: { size?: "default" | "lg"; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-foreground", className)}>
      <EyeIcon
        aria-hidden
        strokeWidth={2.25}
        className={cn("shrink-0 text-primary [&_circle]:fill-current", size === "lg" ? "size-7" : "size-5")}
      />
      <span
        className={cn(
          "font-headline leading-none tracking-[-0.015em]",
          size === "lg" ? "text-[1.625rem]" : "text-[1.1875rem]",
        )}
      >
        FrogEye
      </span>
    </span>
  );
}
