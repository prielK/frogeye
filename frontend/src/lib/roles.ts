import type { Role } from "@/lib/api/client";

type RoleInfo = {
  /** Short label for badges and menus. */
  label: string;
  /** What this reader's briefing leads with (PRODUCT.md, Users). */
  focus: string;
};

export const ROLES: Record<Role, RoleInfo> = {
  analyst: { label: "CI analyst", focus: "Review queue, sources and pipeline health" },
  sales: { label: "Sales", focus: "Competitor moves to know before the next call" },
  pm: { label: "Product", focus: "Competitor releases and capability gaps" },
  exec: { label: "Executive", focus: "Strategic shifts, funding and M&A" },
  pmm_devrel: { label: "PMM & DevRel", focus: "Messaging, launches and community buzz" },
};

export function roleLabel(role: Role): string {
  return ROLES[role]?.label ?? role;
}

/* Briefing editions: master plus one per consumer role (role ids are edition ids). */

/** The API no longer takes an edition (each reader is served their own), so the type is spelled here: the master plus every role but the analyst. */
export type Edition = "master" | Exclude<Role, "analyst">;

/** The five editions, master first; role editions reuse the role's label and focus. */
export const EDITIONS: { value: Edition; label: string; focus: string }[] = [
  { value: "master", label: "Master", focus: "Everything that cleared the bar, for the whole company" },
  { value: "sales", label: ROLES.sales.label, focus: ROLES.sales.focus },
  { value: "pm", label: ROLES.pm.label, focus: ROLES.pm.focus },
  { value: "exec", label: ROLES.exec.label, focus: ROLES.exec.focus },
  { value: "pmm_devrel", label: ROLES.pmm_devrel.label, focus: ROLES.pmm_devrel.focus },
];

export function isEdition(value: unknown): value is Edition {
  return EDITIONS.some((e) => e.value === value);
}

/** A reader's own edition; analysts read the master, as the API defaults. */
export function editionFor(role: Role | undefined): Edition {
  return role && isEdition(role) ? role : "master";
}

export function editionLabel(edition: string): string {
  return EDITIONS.find((e) => e.value === edition)?.label ?? edition;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}
