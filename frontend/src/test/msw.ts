import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { Schemas } from "@/lib/api/client";
import { quietMatrix } from "./compare";
import { capabilityCategories, competitors, costs, emptyOverview, intelReference, kits, quietProductSummaries } from "./fixtures";

export const demoPersonas = [
  { name: "Avery Cohen", email: "analyst@frogeye.local", role: "analyst" },
  { name: "Sam Rivera", email: "sales@frogeye.local", role: "sales" },
  { name: "Priya Shah", email: "pm@frogeye.local", role: "pm" },
  { name: "Eli Morgan", email: "exec@frogeye.local", role: "exec" },
  { name: "Noa Bar", email: "pmm@frogeye.local", role: "pmm_devrel" },
];

/**
 * Default handlers: signed out, demo personas available, and an empty but valid workspace
 * (competitors seeded, nothing collected yet, no briefing filed). Tests override per case
 * with server.use(). `/briefings/latest` is listed before `/briefings/:date`, which would
 * otherwise match it; an override of `:date` must keep that in mind.
 */
export const handlers = [
  http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
  http.get("/api/v1/auth/personas", () => HttpResponse.json(demoPersonas)),
  http.post("/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
  http.get("/api/v1/competitors", () => HttpResponse.json(competitors)),
  // Nothing collected yet: every seeded competitor reads "insufficient", with no board and no move.
  http.get("/api/v1/competitors/overview", () => HttpResponse.json(emptyOverview())),
  // No deep dive unless a test serves one (JFrog itself and unknown slugs are 404s too).
  http.get("/api/v1/competitors/:slug/profile", () => HttpResponse.json({ detail: "Competitor not found" }, { status: 404 })),
  http.get("/api/v1/competitors/:slug/postings", () => HttpResponse.json({ items: [], next_cursor: null })),
  // Nothing collected yet: the 13 products, none under pressure, their seeded competitors listed.
  http.get("/api/v1/products", () => HttpResponse.json(quietProductSummaries())),
  // No product deep dive unless a test serves one (unknown slugs are 404s too).
  http.get("/api/v1/products/:slug", () => HttpResponse.json({ detail: "Product not found" }, { status: 404 })),
  // Nothing collected yet: the seed's pairings, none with evidence.
  http.get("/api/v1/compare", () => HttpResponse.json(quietMatrix())),
  http.get("/api/v1/documents", () => HttpResponse.json({ items: [], next_cursor: null })),
  http.get("/api/v1/sources", () => HttpResponse.json([])),
  http.get("/api/v1/runs", () => HttpResponse.json([])),
  http.get("/api/v1/runs/cooldown", () => HttpResponse.json({ next_manual_run_at: null })),
  http.get("/api/v1/briefings", () => HttpResponse.json([])),
  http.get("/api/v1/briefings/latest", () => HttpResponse.json({ detail: "No briefing yet" }, { status: 404 })),
  http.get("/api/v1/briefings/:date", () => HttpResponse.json({ detail: "No briefing for that date" }, { status: 404 })),
  http.get("/api/v1/signals", () => HttpResponse.json({ items: [], next_cursor: null, search_mode: null })),
  http.get("/api/v1/signals/:id", () => HttpResponse.json({ detail: "Signal not found" }, { status: 404 })),
  http.get("/api/v1/search", () => HttpResponse.json({ plan: null, hits: [], search_mode: "hybrid" })),
  http.get("/api/v1/capabilities", () => HttpResponse.json(capabilityCategories)),
  http.get("/api/v1/kits", () => HttpResponse.json(kits)),
  // The pages' rules at the backend's default settings; nothing collected yet, so nothing bounds the weeks.
  http.get("/api/v1/intel-reference", () => HttpResponse.json(intelReference())),
  http.get("/api/v1/agent-runs", () => HttpResponse.json([])),
  http.get("/api/v1/agent-runs/:id", () => HttpResponse.json({ detail: "Agent run not found" }, { status: 404 })),
  http.get("/api/v1/agent-runs/:id/steps", () => HttpResponse.json([])),
  http.get("/api/v1/costs", () => HttpResponse.json(costs())),
  // A database that did not start from the bundled demo snapshot.
  http.get("/api/v1/demo-snapshot", () => HttpResponse.json({ captured_at: null })),
  // No evaluation results unless a test serves some.
  http.get("/api/v1/evals", () => HttpResponse.json({ detail: "No evaluation results yet" }, { status: 404 })),
];

export const server = setupServer(...handlers);

/** Serve GET /signals/:id from `details` (by id); any other id is a 404, as for a hidden signal. */
export function serveSignalDetails(...details: Schemas["SignalDetail"][]) {
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) => {
      const detail = details.find((d) => d.id === Number(params.id));
      return detail ? HttpResponse.json(detail) : HttpResponse.json({ detail: "Signal not found" }, { status: 404 });
    }),
  );
}

/**
 * EventSource stand-in (jsdom has none, and MSW does not intercept it here). The app's
 * `/api/v1/events` stream is driven from tests with `MockEventSource.emit(type, payload)`,
 * which delivers a frame shaped like the server's (`data` = the full `{type, payload, ts}`).
 */
export class MockEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readyState = MockEventSource.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string | URL, init?: EventSourceInit) {
    super();
    this.url = String(url);
    this.withCredentials = Boolean(init?.withCredentials);
    MockEventSource.instances.push(this);
  }

  /** The connections that are not closed. */
  static get open(): MockEventSource[] {
    return MockEventSource.instances.filter((s) => s.readyState !== MockEventSource.CLOSED);
  }

  /** Send one server frame to every live connection. */
  static emit(type: string, payload: unknown) {
    for (const source of MockEventSource.open) source.emit(type, payload);
  }

  static reset() {
    MockEventSource.instances = [];
  }

  /** The server accepted the stream. */
  accept() {
    this.readyState = MockEventSource.OPEN;
    const event = new Event("open");
    this.onopen?.(event);
    this.dispatchEvent(event);
  }

  /** A dropped or refused stream, as the browser reports it. */
  fail() {
    this.readyState = MockEventSource.CLOSED;
    const event = new Event("error");
    this.onerror?.(event);
    this.dispatchEvent(event);
  }

  emit(type: string, payload: unknown) {
    if (this.readyState === MockEventSource.CONNECTING) this.accept();
    const data = JSON.stringify({ type, payload, ts: new Date().toISOString() });
    this.dispatchEvent(new MessageEvent(type, { data }));
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }
}
