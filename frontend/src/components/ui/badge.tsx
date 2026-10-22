import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import { cn } from "@/lib/utils";

/**
 * Small, square-shouldered labels. Semantic variants carry meaning (stance, health),
 * so pair them with words, never color alone.
 */
const badgeVariants = cva(
  [
    "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-sm border border-transparent px-1.5 py-px",
    "text-meta font-medium whitespace-nowrap transition-colors",
    "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
    "[&>svg]:pointer-events-none [&>svg]:size-3",
  ],
  {
    variants: {
      variant: {
        default: "bg-secondary text-secondary-foreground",
        primary: "bg-primary-soft text-primary-ink",
        secondary: "bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        threat: "bg-threat-soft text-threat",
        opportunity: "bg-opportunity-soft text-opportunity",
        neutral: "bg-neutral-soft text-neutral",
        warning: "bg-warning-soft text-warning",
        destructive: "bg-threat-soft text-threat",
        ghost: "text-muted-foreground [a&]:hover:bg-accent",
        link: "text-primary-ink underline-offset-4 [a&]:hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span";

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
