import type { Schemas } from "@/lib/api/client";
import type { Edition } from "@/lib/roles";
import type { SignalDetail } from "./query";

/*
 * Recommended actions as the reading surfaces show them. The analysis page lists every role's
 * actions in labelled groups; the drawer's quick look and the briefing cards show up to three
 * lines, in the order the newsroom copies them (backend newsroom/fill.py `flattened_actions`):
 * sales talk track, discovery questions, objections, landmines; pm assessment, follow-ups;
 * exec implications, decisions; pmm_devrel messaging, content ideas.
 */

export type ActionRole = "sales" | "pm" | "exec" | "pmm_devrel";
export const ACTION_ROLES: readonly ActionRole[] = ["sales", "pm", "exec", "pmm_devrel"];
export type Level = "high" | "medium" | "low";

export type ActionItem = { kind: "line"; text: string } | { kind: "objection"; objection: string; response: string };
/** A labelled list inside a role's block ("Talk track"); stage-A lines have no label. */
export type ActionGroup = { key: string; label: string | null; items: ActionItem[] };
export type RoleActionSet = { role: ActionRole; groups: ActionGroup[]; risk: Level | null; count: number };

type Actions = Schemas["Actions"];

const QUOTE_PAIRS: Record<string, string> = { '"': '"', "“": "”", "'": "'", "‘": "’" };
const WORD = /[\p{L}\p{N}]/u;

/**
 * Whether the quote opening `t` is the one its last character closes, not one that closes
 * earlier ('"A" and "B"' opens and closes twice). A closing mark between two letters is an
 * apostrophe ("don’t"); a curly pair may nest inside. The backend's newsroom/fill.py `_encloses`.
 */
function encloses(t: string): boolean {
  const opening = t[0]!;
  const closing = t[t.length - 1]!;
  let depth = 0;
  for (let i = 1; i < t.length - 1; i++) {
    const ch = t[i]!;
    if (ch === closing && WORD.test(t[i - 1]!) && WORD.test(t[i + 1]!)) continue; // an apostrophe
    if (ch === closing) {
      if (opening === closing || depth === 0) return false;
      depth -= 1;
    } else if (ch === opening) depth += 1;
  }
  return true;
}

/**
 * The text without ONE level of enclosing straight or curly quotes: the model sometimes
 * quotes a line itself, and the reading surfaces add their own. Only quotes around the whole line
 * go: '"A" and "B"' is kept as written. The backend's newsroom/fill.py `unquote`, case for case, so the analysis page, the drawer, the product page and the briefing cards agree.
 */
export function unquote(text: string): string {
  const t = text.trim();
  return t.length >= 2 && QUOTE_PAIRS[t[0]!] === t[t.length - 1] && encloses(t) ? t.slice(1, -1).trim() : t;
}

function lines(texts: string[] | undefined): ActionItem[] {
  return (texts ?? []).map(unquote).filter(Boolean).map((text) => ({ kind: "line", text }));
}

function objections(pairs: Schemas["Objection"][] | undefined): ActionItem[] {
  return (pairs ?? []).flatMap((p) => {
    const objection = unquote(p.objection);
    return objection ? [{ kind: "objection" as const, objection, response: unquote(p.response) }] : [];
  });
}

function set(role: ActionRole, groups: ActionGroup[], risk: Level | null = null): RoleActionSet {
  const kept = groups.filter((g) => g.items.length > 0);
  return { role, groups: kept, risk, count: kept.reduce((n, g) => n + g.items.length, 0) };
}

/** Every role's actions from a stage-B analysis, in labelled groups. A role with nothing to do is left out. */
export function analysisActionSets(actions: Actions): RoleActionSet[] {
  const { sales, pm, exec, pmm_devrel } = actions;
  const sets = [
    set("sales", [
      { key: "talk_track", label: "Talk track", items: lines(sales.talk_track) },
      { key: "discovery_questions", label: "Discovery questions", items: lines(sales.discovery_questions) },
      { key: "objections", label: "Objections", items: objections(sales.objections) },
      { key: "landmines", label: "Landmine questions", items: lines(sales.landmines) },
    ]),
    set("pm", [
      { key: "assessment", label: "Assessment", items: lines(pm.assessment) },
      { key: "follow_ups", label: "Follow-ups", items: lines(pm.follow_ups) },
    ]),
    set(
      "exec",
      [
        { key: "implications", label: "Implications", items: lines(exec.implications) },
        { key: "decisions", label: "Decisions", items: lines(exec.decisions) },
      ],
      exec.risk_level ?? null,
    ),
    set("pmm_devrel", [
      { key: "messaging", label: "Messaging", items: lines(pmm_devrel.messaging) },
      { key: "content_ideas", label: "Content ideas", items: lines(pmm_devrel.content_ideas) },
    ]),
  ];
  return sets.filter((s) => s.count > 0);
}

/** The stage-A action lines (one per role, may be empty or null), as unlabelled sets. */
export function stageAActionSets(roleActions: SignalDetail["role_actions"]): RoleActionSet[] {
  return ACTION_ROLES.map((role) => set(role, [{ key: "line", label: null, items: lines([roleActions[role] ?? ""]) }])).filter(
    (s) => s.count > 0,
  );
}

/**
 * One action as a single line, as on the briefing cards: an objection pair reads "Objection: … →
 * Response: …", and a talk-track line, being something to say, reads 'Say: "…"'.
 */
export function actionText(item: ActionItem, group?: Pick<ActionGroup, "key">): string {
  if (item.kind === "objection") {
    return item.response ? `Objection: ${item.objection} → Response: ${item.response}` : `Objection: ${item.objection}`;
  }
  return group?.key === "talk_track" ? `Say: “${item.text}”` : item.text;
}

/**
 * Up to `max` lines for one edition: that role's first actions, or for the master the first
 * action of each role that has one, each with its role.
 */
export function quickActions(sets: RoleActionSet[], edition: Edition, max = 3): { role: ActionRole; text: string }[] {
  if (edition === "master") {
    return sets.slice(0, max).map((s) => ({ role: s.role, text: actionText(s.groups[0]!.items[0]!, s.groups[0]) }));
  }
  const own = sets.find((s) => s.role === edition);
  return (own?.groups ?? [])
    .flatMap((g) => g.items.map((item) => ({ item, group: g })))
    .slice(0, max)
    .map(({ item, group }) => ({ role: own!.role, text: actionText(item, group) }));
}
