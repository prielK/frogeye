/// <reference lib="dom" />
/// <reference lib="dom.iterable" />
/**
 * Demo media for the README: a captioned walkthrough (recorded as frames) and the page screenshots.
 * Read-only: it signs in as demo personas and browses. It never presses Run now, never types into
 * a search box and triggers no LLM or embedding call.
 *
 * Run it against the running stack (web app on :8080, Mailpit on :8025), from the repo root
 * (about 5 minutes; .demo-work/ is gitignored):
 *
 *   MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm \
 *     -v ./.demo-work:/media-work -e DEMO_OUT=/media-work \
 *     web-e2e npx playwright test -c playwright.demo.config.ts
 *   sh frontend/e2e/demo/encode.sh .demo-work media
 *
 * The first command writes, under DEMO_OUT, the JPEG frames, `video.ffconcat` (the kept cuts),
 * `beats.json` (each caption and its time in the final video), `preview.txt` (the clips of the
 * README loop) and `screenshots/*.png`. The second encodes `media/demo.mp4` and
 * `media/demo-preview.gif` and copies the optimised screenshots; ffmpeg and oxipng run in
 * throwaway containers. `-g walkthrough` or `-g screenshots` re-records one half.
 * The featured story, run and cell are pinned below: check them against the day's data first.
 */
import { test, type Browser, type Locator, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const OUT = process.env.DEMO_OUT ?? "demo-out";
const MAIL = process.env.MAILPIT_URL ?? "http://host.docker.internal:8025";
const PASSWORD = "frogeye-demo";
const ANALYST = "analyst@frogeye.local";
const SALES = "sales@frogeye.local";

// The featured story, run and cell (live data on 22 Oct 2026).
const STORY_CARD = "Azure DevOps adds agentic fixes for dependency and code alerts";
const STORY_ID = 320;
const RUN_ID = 8;
const RUN_STEP = "Step #750";
// two steps above the featured call, so the trace screenshot opens on a whole step
const RUN_SHOT_STEP = "Step #748";

// The README preview loop, cut from the video: [caption the clip starts from, offset s, length s].
const PREVIEW: [string, number, number][] = [
  ["Each story:", 0.2, 3.6],
  ["Every claim cites", 0.3, 7.6],
  ["Two high threats", -2.4, 5.6],
  ["Search quality", 0.1, 4.2],
];

const FONT_FILE = join(process.cwd(), "node_modules/@fontsource-variable/schibsted-grotesk/files/schibsted-grotesk-latin-wght-normal.woff2");
const FONT_B64 = existsSync(FONT_FILE) ? readFileSync(FONT_FILE).toString("base64") : "";

test.describe.configure({ mode: "serial" });

async function login(page: Page, email: string) {
  const res = await page.request.post("/api/v1/auth/login", { data: { email, password: PASSWORD } });
  if (!res.ok()) throw new Error(`sign-in as ${email} failed: ${res.status()}`);
}

async function settle(page: Page, ms = 700) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(ms);
}

/* ---------------------------------------------------------------- recording */

type Frame = { t: number; file: string };

/** Records CDP screencast frames and the time windows to keep (cuts drop page loads). */
class Recorder {
  frames: Frame[] = [];
  keep: [number, number][] = [];
  beats: { t: number; text: string }[] = [];
  private open: number | null = null;
  private n = 0;
  private dir = join(OUT, "frames");

  async start(page: Page) {
    rmSync(this.dir, { recursive: true, force: true });
    mkdirSync(this.dir, { recursive: true });
    const cdp = await page.context().newCDPSession(page);
    cdp.on("Page.screencastFrame", (frame) => {
      const t = Date.now() / 1000;
      const file = `frames/${String(this.n++).padStart(6, "0")}.jpg`;
      writeFileSync(join(OUT, file), Buffer.from(frame.data, "base64"));
      this.frames.push({ t, file });
      cdp.send("Page.screencastFrameAck", { sessionId: frame.sessionId }).catch(() => undefined);
    });
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1440, maxHeight: 900, everyNthFrame: 1 });
    return cdp;
  }

  cutIn() {
    if (this.open === null) this.open = Date.now() / 1000;
  }

  cutOut() {
    if (this.open !== null) this.keep.push([this.open, Date.now() / 1000]);
    this.open = null;
  }

  /** Position of wall-clock time `t` on the final video's timeline. */
  private outTime(t: number) {
    let acc = 0;
    for (const [a, b] of this.keep) {
      if (t < a) return acc;
      if (t <= b) return acc + (t - a);
      acc += b - a;
    }
    return acc;
  }

  mark(text: string) {
    this.beats.push({ t: Date.now() / 1000, text });
  }

  write() {
    this.cutOut();
    const lines = ["ffconcat version 1.0"];
    let last = "";
    for (const [a, b] of this.keep) {
      const before = this.frames.filter((f) => f.t <= a).at(-1);
      const inside = this.frames.filter((f) => f.t > a && f.t <= b);
      const seq: Frame[] = [...(before ? [{ t: a, file: before.file }] : []), ...inside];
      seq.forEach((f, i) => {
        const end = i + 1 < seq.length ? seq[i + 1].t : b;
        const d = end - Math.max(f.t, a);
        if (d <= 0) return;
        lines.push(`file '${f.file}'`, `duration ${d.toFixed(4)}`);
        last = f.file;
      });
    }
    if (last) lines.push(`file '${last}'`);
    writeFileSync(join(OUT, "video.ffconcat"), lines.join("\n") + "\n");
    const total = this.keep.reduce((s, [a, b]) => s + (b - a), 0);
    const beats = this.beats.map((b) => ({ at: Number(this.outTime(b.t).toFixed(1)), text: b.text }));
    writeFileSync(join(OUT, "beats.json"), JSON.stringify({ duration: Number(total.toFixed(1)), beats }, null, 2));
    const clips = PREVIEW.map(([prefix, offset, length]) => {
      const beat = beats.find((b) => b.text.startsWith(prefix));
      if (!beat) throw new Error(`preview: no caption starts with "${prefix}"`);
      return `${Math.max(0, beat.at + offset).toFixed(2)} ${length}`;
    });
    writeFileSync(join(OUT, "preview.txt"), clips.join("\n") + "\n");
  }
}

/* ---------------------------------------------------------------- overlays */

/** A soft cursor dot that follows the (otherwise invisible) headless mouse, with a click pulse. */
const CURSOR_SCRIPT = `(() => {
  if (window.top !== window) return;
  const install = () => {
    if (document.getElementById("demo-cursor")) return;
    const dot = document.createElement("div");
    dot.id = "demo-cursor";
    Object.assign(dot.style, {
      position: "fixed", left: "0px", top: "0px", width: "22px", height: "22px", margin: "-11px 0 0 -11px",
      borderRadius: "50%", background: "rgba(20, 20, 20, 0.28)", border: "2px solid rgba(255, 255, 255, 0.95)",
      boxShadow: "0 1px 4px rgba(0, 0, 0, 0.35)", pointerEvents: "none", zIndex: "2147483646",
      transition: "transform 120ms ease-out, opacity 200ms", opacity: "0",
    });
    document.documentElement.appendChild(dot);
    const pos = window.__demoMouse || { x: -100, y: -100 };
    dot.style.transform = "translate(" + pos.x + "px, " + pos.y + "px)";
    window.addEventListener("mousemove", (e) => {
      window.__demoMouse = { x: e.clientX, y: e.clientY };
      dot.style.opacity = "1";
      dot.style.transform = "translate(" + e.clientX + "px, " + e.clientY + "px)";
    }, true);
    window.addEventListener("mousedown", () => { dot.style.transform += " scale(0.7)"; }, true);
    window.addEventListener("mouseup", () => { dot.style.transform = dot.style.transform.replace(" scale(0.7)", ""); }, true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install); else install();
})();`;

/** Shows (or replaces) the caption bar at the bottom centre. CSSOM only, so a strict CSP allows it. */
type CaptionOpts = { instant?: boolean; top?: boolean };

async function caption(page: Page, rec: Recorder, text: string, opts: CaptionOpts = {}) {
  rec.mark(text);
  await page.evaluate(
    async ({ text, font, instant, top }) => {
      const w = window as unknown as { __demoFont?: boolean };
      if (font && !w.__demoFont) {
        const bytes = Uint8Array.from(atob(font), (c) => c.charCodeAt(0));
        const face = new FontFace("DemoCaption", bytes.buffer, { weight: "100 900" });
        await face.load();
        document.fonts.add(face);
        w.__demoFont = true;
      }
      let bar = document.getElementById("demo-caption");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "demo-caption";
        Object.assign(bar.style, {
          position: "fixed", left: "50%", bottom: "36px", transform: "translateX(-50%)", maxWidth: "1040px",
          width: "max-content", padding: "14px 30px", borderRadius: "12px", background: "rgba(16, 18, 17, 0.9)",
          color: "#ffffff", font: "560 27px/1.35 DemoCaption, 'Schibsted Grotesk Variable', system-ui, sans-serif",
          letterSpacing: "0.005em", textAlign: "center", textWrap: "balance", boxShadow: "0 6px 24px rgba(0, 0, 0, 0.28)",
          pointerEvents: "none", zIndex: "2147483647", opacity: "0", transition: "opacity 280ms ease",
        });
        document.documentElement.appendChild(bar);
      }
      const show = () => {
        // bottom centre; at the top only when the bottom holds what the beat is about
        bar.style.top = top ? "76px" : "auto";
        bar.style.bottom = top ? "auto" : "36px";
        bar.textContent = text;
        bar.style.opacity = "1";
      };
      if (instant || bar.style.opacity === "0" || !bar.textContent) {
        bar.style.transition = instant ? "none" : "opacity 280ms ease";
        show();
        void bar.offsetWidth;
        bar.style.transition = "opacity 280ms ease";
      } else {
        bar.style.opacity = "0";
        await new Promise((r) => setTimeout(r, 300));
        show();
      }
    },
    { text, font: FONT_B64, instant: opts.instant ?? false, top: opts.top ?? false },
  );
}

async function hideCaption(page: Page) {
  await page.evaluate(() => {
    const bar = document.getElementById("demo-caption");
    if (bar) bar.style.opacity = "0";
  });
}

/** A full-screen title card over the page. */
async function card(page: Page, title: string, line: string, show: boolean) {
  await page.evaluate(
    async ({ title, line, show, font }) => {
      const w = window as unknown as { __demoFont?: boolean };
      if (font && !w.__demoFont) {
        const bytes = Uint8Array.from(atob(font), (c) => c.charCodeAt(0));
        const face = new FontFace("DemoCaption", bytes.buffer, { weight: "100 900" });
        await face.load();
        document.fonts.add(face);
        w.__demoFont = true;
      }
      let el = document.getElementById("demo-card");
      if (!show) {
        if (el) {
          el.style.opacity = "0";
          await new Promise((r) => setTimeout(r, 650));
          el.remove();
        }
        return;
      }
      el?.remove();
      el = document.createElement("div");
      el.id = "demo-card";
      Object.assign(el.style, {
        position: "fixed", inset: "0", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        gap: "22px", background: "#0f1a14", color: "#ffffff", zIndex: "2147483647", opacity: "1",
        transition: "opacity 600ms ease", fontFamily: "DemoCaption, system-ui, sans-serif", textAlign: "center", padding: "0 120px",
      });
      const h = document.createElement("div");
      h.textContent = title;
      Object.assign(h.style, { font: "700 76px/1.1 DemoCaption, system-ui, sans-serif", letterSpacing: "-0.01em" });
      const dot = document.createElement("span");
      Object.assign(dot.style, { display: "inline-block", width: "22px", height: "22px", borderRadius: "50%", background: "#3fb950", marginRight: "22px", verticalAlign: "middle", position: "relative", top: "-6px" });
      h.prepend(dot);
      const p = document.createElement("div");
      p.textContent = line;
      Object.assign(p.style, { font: "460 32px/1.45 DemoCaption, system-ui, sans-serif", color: "rgba(255,255,255,0.86)", maxWidth: "1000px", textWrap: "balance" });
      el.append(h, p);
      document.documentElement.appendChild(el);
    },
    { title, line, show, font: FONT_B64 },
  );
}

/* ---------------------------------------------------------------- motion */

const ease = (p: number) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
let mouse = { x: 120, y: 720 };

async function moveTo(page: Page, x: number, y: number, ms = 650) {
  const from = { ...mouse };
  const steps = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= steps; i++) {
    const e = ease(i / steps);
    await page.mouse.move(from.x + (x - from.x) * e, from.y + (y - from.y) * e);
    await page.waitForTimeout(ms / steps);
  }
  mouse = { x, y };
}

async function pointAt(page: Page, target: Locator, ms = 650) {
  const box = await target.boundingBox();
  if (!box) throw new Error(`no box for ${target}`);
  await moveTo(page, box.x + box.width / 2, box.y + box.height / 2, ms);
}

async function clickOn(page: Page, target: Locator) {
  await pointAt(page, target);
  await page.waitForTimeout(250);
  // a real click at the same point (Radix selects ignore a bare down/up pair)
  await target.click({ delay: 90 });
}

/** Eased window scroll to an absolute offset. */
async function scrollTo(page: Page, y: number, ms = 1400) {
  await page.evaluate(
    ({ y, ms }) =>
      new Promise<void>((done) => {
        const el = document.scrollingElement ?? document.documentElement;
        const start = el.scrollTop;
        const end = Math.max(0, Math.min(y, el.scrollHeight - window.innerHeight));
        const t0 = performance.now();
        const step = (t: number) => {
          const p = Math.min(1, (t - t0) / ms);
          const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
          el.scrollTo({ top: start + (end - start) * e, behavior: "instant" });
          if (p < 1) requestAnimationFrame(step);
          else done();
        };
        requestAnimationFrame(step);
      }),
    { y, ms },
  );
}

/** Scrolls so `target` sits `offset` px below the top bar. */
async function scrollToEl(page: Page, target: Locator, offset = 90, ms = 1400) {
  const top = await target.evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
  await scrollTo(page, top - offset, ms);
}

/** Click a link, drop the load from the video, resume once `ready` shows. */
async function go(page: Page, rec: Recorder, link: Locator, ready: Locator) {
  await clickOn(page, link);
  rec.cutOut();
  await ready.first().waitFor();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await settle(page);
}

/** Eased scroll of the Mailpit message pane (whichever element around the preview scrolls). */
async function scrollMail(page: Page, dy: number, ms: number) {
  await page.locator("iframe").first().evaluate(
    (frame, { dy, ms }) =>
      new Promise<void>((done) => {
        let el: HTMLElement | null = frame.parentElement;
        while (el && !(el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
        const doc = (frame as HTMLIFrameElement).contentDocument;
        const target = el ?? (doc?.scrollingElement as HTMLElement | null) ?? (document.scrollingElement as HTMLElement);
        const start = target.scrollTop;
        const t0 = performance.now();
        const step = (t: number) => {
          const p = Math.min(1, (t - t0) / ms);
          const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
          target.scrollTop = start + dy * e;
          if (p < 1) requestAnimationFrame(step);
          else done();
        };
        requestAnimationFrame(step);
      }),
    { dy, ms },
  );
}

/** The open message's HTML body in Mailpit (an iframe). */
const mailBody = (page: Page) => page.frameLocator("iframe").first();

/* ---------------------------------------------------------------- the walkthrough */

test("walkthrough", async ({ page }) => {
  test.setTimeout(20 * 60_000);
  mkdirSync(OUT, { recursive: true });
  const rec = new Recorder();
  const hold = (s: number) => page.waitForTimeout(s * 1000);
  const say = (text: string, opts: CaptionOpts = {}) => caption(page, rec, text, opts);
  const heading = (name: string | RegExp, level = 1) => page.getByRole("heading", { name, level });
  const nav = (name: string) => page.getByRole("complementary").getByRole("link", { name, exact: true });
  /** Off camera: load `url` (if given), then come back on camera with `text` already showing. */
  const cutTo = async (ready: Locator, text: string, url?: string) => {
    rec.cutOut();
    if (url) await page.goto(url);
    await ready.first().waitFor();
    await settle(page, 900);
    await say(text, { instant: true });
    rec.cutIn();
  };
  /** Click a link on camera, cut the load, resume on the new page with `text`. */
  const follow = async (link: Locator, ready: Locator, text: string) => {
    await go(page, rec, link, ready);
    await say(text, { instant: true });
    rec.cutIn();
  };

  await page.addInitScript(CURSOR_SCRIPT);
  await login(page, ANALYST);
  await page.goto("/briefing");
  await heading("Morning Briefing").waitFor();
  await settle(page, 1200);
  await card(page, "FrogEye", "Competitive intelligence for JFrog: AI agents turn public signals into a daily, cited briefing.", true);
  await page.mouse.move(mouse.x, mouse.y);
  await rec.start(page);
  await page.waitForTimeout(400);

  // 1. Title card
  rec.cutIn();
  rec.mark("[Title card] FrogEye. Competitive intelligence for JFrog: AI agents turn public signals into a daily, cited briefing.");
  await hold(4);
  await card(page, "", "", false);

  // 2. Briefing, master edition, then a role edition
  await say("Every morning, the Briefing: the day's most important competitor moves.");
  await hold(4);
  await scrollToEl(page, page.getByRole("link", { name: STORY_CARD }), 110, 1600);
  await say("Each story: what happened, why it matters for JFrog, and what to do.");
  await hold(4);
  rec.cutOut();
  await login(page, SALES);
  await cutTo(page.getByText("Sales edition"), "Each role gets its own edition. Here is the Sales edition.", "/briefing");
  await hold(4);
  await scrollToEl(page, page.getByRole("link", { name: STORY_CARD }), 110, 1500);
  await say("Sales gets talk tracks and discovery questions for the next call.");
  await hold(4);

  // 3. Back to the master edition: the top story's analysis
  rec.cutOut();
  await login(page, ANALYST);
  await page.goto("/briefing");
  await heading("Morning Briefing").waitFor();
  await settle(page, 900);
  const storyLink = page.getByRole("link", { name: STORY_CARD });
  await scrollToEl(page, storyLink, 110, 1);
  await moveTo(page, 560, 300, 1);
  await cutTo(storyLink, "Back to the master edition. Open the top story.");
  await hold(1.5);
  await follow(storyLink, page.getByRole("heading", { name: "What happened" }), "The full analysis reads like a short article, from the story's own sources.");
  await hold(3);
  await scrollToEl(page, page.getByRole("heading", { name: "What happened" }), 100, 1500);
  await hold(2.5);
  await scrollToEl(page, page.getByRole("heading", { name: "Impact on JFrog" }), 90, 1400);
  await say("The impact on each JFrog product, then actions for each role.");
  await hold(4);

  // 4. A citation jumps to its source (the second [2]: the Xray impact line)
  const cite = page.getByRole("link", { name: /^Source 2/ }).nth(1);
  await say("Every claim cites its source; click a number to see it.");
  await pointAt(page, cite, 800);
  await hold(2);
  await clickOn(page, cite);
  await say("This claim rests on JFrog's own Xray product page.", { top: true });
  await hold(3.5);

  // 5. Signals Explorer: a filter and the quick look (no search: it would call the embedding API)
  await follow(nav("Signals"), heading("Signals"), "The Signals Explorer: every analysed story, most important first.");
  await hold(3.2);
  await clickOn(page, page.getByRole("combobox", { name: "Competitor" }));
  await say("Filter by competitor, type, stance, importance or date.");
  await hold(1.6);
  await clickOn(page, page.getByRole("option", { name: "GitHub", exact: true }));
  await settle(page, 400);
  await hold(1.2);
  await clickOn(page, page.locator("[data-row-target]").first());
  await page.getByRole("link", { name: /Open full analysis/ }).waitFor();
  await say("A quick look: summary, actions and sources, without leaving the list.");
  await hold(4);
  await page.keyboard.press("Escape");
  await hold(0.6);

  // 6. Competitors and one deep dive
  await follow(nav("Competitors"), heading("Competitors"), "Competitors: 23 tracked, each with its latest move this week.");
  await hold(4);
  await scrollToEl(page, page.getByText("Competitive landscape").first(), 90, 1500);
  await say("The landscape: overlap with JFrog against weekly momentum.");
  await pointAt(page, page.getByRole("link", { name: /^Sonatype, Overlap with JFrog/ }), 900);
  await hold(2.5);
  await scrollToEl(page, page.getByText("All competitors").first(), 90, 1500);
  await say("The table ranks threat to JFrog, momentum, open roles and latest move.");
  await hold(4);
  await follow(page.getByRole("link", { name: "Sonatype", exact: true }), heading("Sonatype"), "A deep dive per competitor: a cited bottom line and key figures.");
  await hold(3.5);
  const sections = page.getByRole("navigation", { name: "Sections" });
  await clickOn(page, sections.getByRole("link", { name: "Products vs JFrog" }));
  await hold(1);
  await say("Their products against JFrog's, with the overlap the evidence shows.");
  await hold(3.5);
  await clickOn(page, sections.getByRole("link", { name: "People & hiring" }));
  await hold(1);
  await say("Hiring from public job boards: the capabilities their open roles name.");
  await scrollToEl(page, page.getByText("Capabilities in its open roles").first(), 330, 1400);
  await hold(3);

  // 7. Products and one deep dive
  await follow(nav("Products"), heading("Products"), "Products: JFrog's 13 products, most pressed first, with top competitors.");
  await hold(4);
  await follow(page.getByRole("link", { name: "Curation", exact: true }).first(), heading("Curation"), "Curation's deep dive: its bottom line, pressure and who competes with it.");
  await hold(2);
  await scrollToEl(page, page.getByRole("heading", { name: "Competitors", level: 2 }), 90, 1500);
  await hold(3);

  // 8. Compare
  await follow(nav("Compare"), heading("Compare"), "Compare: 13 JFrog products against 17 competitors' products.");
  await hold(3.2);
  await pointAt(page, page.locator('table [data-cell="xray:github"]'), 900);
  await say("Each cell: their products, the threat named, and the latest move.");
  await hold(2);
  const high = page.locator('table [data-cell="curation:sonatype"]');
  await pointAt(page, high, 900);
  await hold(1.2);
  await clickOn(page, high);
  await page.getByRole("dialog").waitFor();
  await say("Two high threats. This one: Sonatype Firewall against Curation, with its evidence.");
  await hold(5);
  await page.keyboard.press("Escape");
  await hold(0.6);

  // 9. Agent runs: one run's trace, then AI spend
  await follow(nav("Agent runs"), heading("Agent runs"), "Admin: every run of the daily agent graph, with what it cost.");
  await hold(3.2);
  await follow(page.getByRole("link", { name: new RegExp(`^Run ${RUN_ID}, started`) }), heading(/^Daily run/), "This run read 460 items and wrote 40 full analyses for $0.25.");
  await hold(4);
  await pointAt(page, page.getByText("One stage reported an error"), 900);
  await say("An analysis that fails its citation checks is not stored.");
  await hold(3.2);
  await scrollToEl(page, page.getByText(RUN_STEP, { exact: true }), 260, 1500);
  await pointAt(page, page.getByText(RUN_STEP, { exact: true }), 700);
  await say("Every AI call is traced: model, tokens, cost and latency.");
  await hold(4);
  await go(page, rec, nav("Agent runs"), heading("Agent runs"));
  await scrollToEl(page, page.getByRole("heading", { name: "AI spend" }), 90, 1);
  await say("AI spend: today against the daily cap, by task and by model.", { instant: true });
  rec.cutIn();
  await hold(4);

  // 10. Evals
  await follow(nav("Evals"), heading("Evals"), "Evals: models and guards measured on 338 labelled cases.");
  await hold(4);
  await scrollToEl(page, page.getByRole("heading", { name: "Triage", level: 2 }), 90, 1400);
  await say("Triage keeps 96.7% of relevant documents, on 60 real cases.");
  await hold(4);
  await scrollToEl(page, page.getByRole("heading", { name: "Retrieval", level: 2 }), 90, 1400);
  await say("Search quality: hybrid recall 0.967, keyword-only 0.618.");
  await hold(3.6);
  // failure lists in page order: triage, disambiguation, retrieval
  const failures = page.locator("summary", { hasText: "failures" }).nth(2);
  await clickOn(page, failures);
  await hold(0.6);
  await scrollToEl(page, failures, 300, 1200);
  await say("Every failure is listed, so you see exactly what it got wrong.");
  await hold(4);

  // 11. Mailpit: the briefing email
  await cutTo(page.getByText(`To: ${SALES}`), "Each briefing is emailed too: one edition per persona.", MAIL);
  await hold(3.2);
  await follow(page.getByText(`To: ${SALES}`).first(), mailBody(page).getByText("Sales edition"), "The Sales edition, as Sam Rivera receives it, every story linked.");
  await hold(2.2);
  await scrollMail(page, 460, 1700);
  await hold(1.5);

  // 12. Closing card
  await hideCaption(page);
  await card(page, "FrogEye", "One docker compose up. It runs daily at 06:00, for about $0.25 a day.", true);
  rec.mark("[Closing card] One docker compose up. It runs daily at 06:00, for about $0.25 a day.");
  await hold(4.6);
  rec.write();
});

/* ---------------------------------------------------------------- screenshots */

async function shoot(page: Page, name: string) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(OUT, "screenshots", `${name}.png`), animations: "disabled", caret: "hide" });
}

async function freshPage(browser: Browser, email: string, opts: { dark?: boolean; mobile?: boolean } = {}) {
  const context = await browser.newContext({
    baseURL: process.env.BASE_URL ?? "http://localhost:8080",
    viewport: opts.mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    isMobile: opts.mobile ?? false,
    hasTouch: opts.mobile ?? false,
    colorScheme: opts.dark ? "dark" : "light",
    timezoneId: "Asia/Jerusalem",
    locale: "en-US",
  });
  const theme = opts.dark ? "dark" : "light";
  await context.addInitScript((t) => {
    try {
      window.localStorage.setItem("frogeye-theme", t);
    } catch {
      // storage blocked: the default (light) applies
    }
  }, theme);
  const page = await context.newPage();
  await login(page, email);
  return page;
}

test("screenshots", async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  mkdirSync(join(OUT, "screenshots"), { recursive: true });
  const page = await freshPage(browser, ANALYST);
  const heading = (name: string | RegExp, level = 1) => page.getByRole("heading", { name, level });
  const open = async (url: string, ready: Locator) => {
    await page.goto(url);
    await ready.first().waitFor();
    await settle(page, 900);
  };
  const top = async (target: Locator, offset = 90) => {
    const y = await target.first().evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
    await page.evaluate((v) => window.scrollTo({ top: v, behavior: "instant" }), y - offset);
  };

  await open("/briefing", heading("Morning Briefing"));
  await shoot(page, "briefing");

  await open(`/signals/${STORY_ID}`, page.getByRole("heading", { name: "What happened" }));
  await shoot(page, "signal-analysis");

  await open("/signals?competitor=github", heading("Signals"));
  if (!(await page.getByRole("combobox", { name: "Competitor" }).textContent())?.includes("GitHub")) {
    await page.getByRole("combobox", { name: "Competitor" }).click();
    await page.getByRole("option", { name: "GitHub", exact: true }).click();
    await settle(page, 400);
  }
  await page.locator("[data-row-target]").first().click();
  await page.getByRole("link", { name: /Open full analysis/ }).waitFor();
  await shoot(page, "signals-explorer");
  await page.keyboard.press("Escape");

  await open("/competitors", heading("Competitors"));
  await top(page.getByText("Competitive landscape"), 80);
  await shoot(page, "competitors");

  await open("/competitors/sonatype", heading("Sonatype"));
  await shoot(page, "competitor-deep-dive");

  await open("/products", heading("Products"));
  await shoot(page, "products");

  await open("/products/curation", heading("Curation"));
  await shoot(page, "product-deep-dive");

  await open("/compare", heading("Compare"));
  await shoot(page, "compare");
  await page.locator('table [data-cell="curation:sonatype"]').click();
  await page.getByRole("dialog").waitFor();
  await shoot(page, "compare-cell");
  await page.keyboard.press("Escape");

  await open(`/admin/agents/${RUN_ID}`, heading(/^Daily run/));
  await top(page.getByText(RUN_SHOT_STEP, { exact: true }), 80);
  await shoot(page, "agent-run-trace");

  await open("/admin/agents", heading("Agent runs"));
  await top(page.getByRole("heading", { name: "AI spend" }), 80);
  await shoot(page, "ai-spend");

  await open("/admin/evals", heading("Evals"));
  await shoot(page, "evals");

  await page.goto(MAIL);
  await page.getByText(`To: ${SALES}`).first().click();
  await mailBody(page).getByText("Sales edition").first().waitFor();
  await page.waitForTimeout(1500);
  await shoot(page, "email");
  await page.context().close();

  const sales = await freshPage(browser, SALES);
  await sales.goto("/briefing");
  await sales.getByText("Sales edition").first().waitFor();
  await settle(sales, 900);
  await sales.screenshot({ path: join(OUT, "screenshots", "briefing-edition.png"), animations: "disabled", caret: "hide" });
  await sales.context().close();

  const dark = await freshPage(browser, ANALYST, { dark: true });
  await dark.goto("/compare");
  await dark.getByRole("heading", { name: "Compare", level: 1 }).waitFor();
  await settle(dark, 900);
  await dark.screenshot({ path: join(OUT, "screenshots", "compare-dark.png"), animations: "disabled", caret: "hide" });
  await dark.context().close();

  const mobile = await freshPage(browser, ANALYST, { mobile: true });
  await mobile.goto("/briefing");
  await mobile.getByRole("heading", { name: "Morning Briefing", level: 1 }).waitFor();
  await settle(mobile, 900);
  await mobile.screenshot({ path: join(OUT, "screenshots", "briefing-mobile.png"), animations: "disabled", caret: "hide" });
  await mobile.context().close();
});
