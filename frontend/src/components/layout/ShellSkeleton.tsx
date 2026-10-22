import { Wordmark } from "@/components/layout/Wordmark";
import { Skeleton } from "@/components/ui/skeleton";

/** Shown while the session is checked: the shell's real geometry, content still settling. */
export function ShellSkeleton() {
  return (
    <div role="status" aria-label="Loading FrogEye" className="min-h-dvh bg-background">
      <div className="flex h-14 border-b">
        <div className="flex shrink-0 items-center px-4 md:w-60 md:border-r md:border-sidebar-border md:bg-sidebar">
          <Wordmark />
        </div>
        <div className="flex flex-1 items-center justify-end gap-3 px-4 md:px-6 lg:px-10">
          <Skeleton className="size-6 rounded-full" />
          <Skeleton className="h-4 w-28" />
        </div>
      </div>
      <div className="flex">
        <div className="hidden h-[calc(100dvh-3.5rem)] w-60 shrink-0 flex-col gap-2 border-r border-sidebar-border bg-sidebar p-3 md:flex">
          <Skeleton className="h-8 w-full bg-sidebar-accent" />
          <Skeleton className="mt-6 h-3 w-16 bg-sidebar-accent" />
          <Skeleton className="h-8 w-full bg-sidebar-accent" />
          <Skeleton className="h-8 w-full bg-sidebar-accent" />
        </div>
        <div className="flex flex-1 flex-col gap-3 px-4 py-8 md:px-6 lg:px-10">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-full max-w-md" />
        </div>
      </div>
    </div>
  );
}
