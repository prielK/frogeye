import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { MockEventSource, server } from "./msw";

// The longest pages (the product list, the deep dives) take about a second to first render
// in jsdom, which is the default wait of `findBy*`; under a parallel run that flakes. The
// queries still resolve as soon as the element appears; vitest's 5 s test timeout stays the cap.
configure({ asyncUtilTimeout: 4000 });

// Browsers resolve relative request URLs against the page; Node's Request does not.
// openapi-fetch builds `new Request("/api/...")`, so resolve against jsdom's origin.
const NativeRequest = globalThis.Request;
globalThis.Request = class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const resolved =
      typeof input === "string" && input.startsWith("/") ? new URL(input, window.location.origin) : input;
    super(resolved, init);
  }
} as typeof Request;

// jsdom gaps that Radix primitives touch.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
window.matchMedia ??= (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;
Element.prototype.scrollIntoView ??= () => {};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.setPointerCapture ??= () => {}; // sonner's toast swipe handling
// The live-events stream (lib/events.ts) opens one EventSource per signed-in app.
globalThis.EventSource = MockEventSource as unknown as typeof EventSource;

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  MockEventSource.reset();
  server.resetHandlers();
  localStorage.clear();
  document.documentElement.className = "";
  document.documentElement.removeAttribute("style");
});
afterAll(() => server.close());
