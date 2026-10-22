import { useId } from "react";
import { ACTION_ROLES, unquote } from "@/components/signal/actions";
import { ProfileSection } from "@/features/intel/grammar";
import { plural } from "@/lib/format";
import { ROLES } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { COMES_WITH_BRIEF, QUIET, section, type SectionProps } from "./shared";

/* What to do: every role's actions from the brief, the same for every reader; never a role switch. */

function RoleBlock({ label, items, index }: { label: string; items: string[]; index: number }) {
  const id = useId();
  return (
    // Hairlines part the blocks, never one under the section's ink rule (the first, or the second beside it).
    <div className={cn("flex min-w-0 flex-col gap-2", index > 0 && "border-t pt-4", index === 1 && "@[44rem]:border-t-0 @[44rem]:pt-0")}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h3 id={id} className="text-body font-semibold text-foreground">
          {label}
        </h3>
        {items.length ? <span className="text-meta text-muted-foreground">{plural(items.length, "action")}</span> : null}
      </div>
      {items.length > 0 ? (
        <ul aria-labelledby={id} className="flex max-w-[60ch] list-disc flex-col gap-1.5 pl-[1.125rem] marker:text-muted-foreground">
          {items.map((text, i) => (
            <li key={i} className="text-body text-pretty text-foreground">
              {text}
            </li>
          ))}
        </ul>
      ) : (
        <p className={QUIET}>{`No action for ${label} in this brief.`}</p>
      )}
    </div>
  );
}

export function ActionsSection({ profile }: SectionProps) {
  const { brief } = profile;
  return (
    <ProfileSection section={section("what-to-do")} meta={brief ? "From the brief, for every team" : undefined}>
      {!brief ? (
        <p className={QUIET}>{COMES_WITH_BRIEF}</p>
      ) : (
        <div className="grid gap-x-12 gap-y-6 @[44rem]:grid-cols-2">
          {ACTION_ROLES.map((role, i) => (
            <RoleBlock key={role} index={i} label={ROLES[role].label} items={brief.actions[role].map(unquote).filter(Boolean)} />
          ))}
        </div>
      )}
    </ProfileSection>
  );
}
