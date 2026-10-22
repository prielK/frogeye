import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Profiler } from "react";
import css from "@/styles/globals.css?raw";
import pkg from "/package.json?raw";
import { BarList } from "./BarList";
import { BubbleScatter } from "./BubbleScatter";
import { ChartFrame } from "./ChartFrame";
import { DivergingBars } from "./DivergingBars";
import { DotTimeline } from "./DotTimeline";
import { LineChart } from "./LineChart";
import { dayDate, textWidth } from "./scales";
import { Sparkline } from "./Sparkline";
import { StackedArea } from "./StackedArea";
import { StepLine } from "./StepLine";
import { Swimlane } from "./Swimlane";
import { ChartTooltip } from "./tooltip";

/*
 * The chart kit. Every chart prints a one-line summary, draws one mark per datum,
 * shows exact values in a tooltip on focus (the same as hover), renders a real empty state for
 * empty data and wears only the chart tokens and the four type sizes.
 */

const d = dayDate;
const empty = { title: "Nothing measured yet", hint: "This fills in after the first collection." };
const marks = (container: HTMLElement, kind?: string) =>
  [...container.querySelectorAll<HTMLElement | SVGElement>(kind ? `[data-mark][data-kind="${kind}"]` : "[data-mark]")];
const focus = (el: Element) => act(() => (el as HTMLElement).focus());
const tooltip = () => screen.getByRole("tooltip");
const usdM = (n: number) => `$${n / 1e6}M`;

describe("ChartFrame", () => {
  it("is a figure named by its title, with the printed summary in the caption above the chart", () => {
    render(
      <ChartFrame title="Open roles" summary="142 open now, up 12 in 30 days">
        <svg data-testid="plot" />
      </ChartFrame>,
    );
    const figure = screen.getByRole("figure", { name: "Open roles" });
    const caption = figure.querySelector("figcaption")!;
    expect(caption).toHaveTextContent("Open roles");
    expect(caption).toHaveTextContent("142 open now, up 12 in 30 days");
    // The caption comes first: the summary is read before the chart.
    expect(caption.compareDocumentPosition(screen.getByTestId("plot")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders the empty state instead of the chart and its summary", () => {
    render(
      <ChartFrame title="Open roles" summary="142 open now" empty={empty}>
        <svg data-testid="plot" />
      </ChartFrame>,
    );
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(screen.getByText("This fills in after the first collection.")).toBeInTheDocument();
    expect(screen.queryByTestId("plot")).toBeNull();
    expect(screen.queryByText("142 open now")).toBeNull();
    expect(screen.getByRole("figure", { name: "Open roles" })).toBeInTheDocument();
  });
});

describe("LineChart", () => {
  const series = [
    { key: "total", label: "All roles", points: [{ x: d("2026-10-01"), y: 130 }, { x: d("2026-10-02"), y: 1240 }, { x: d("2026-10-03"), y: 142 }] },
    { key: "eng", label: "Engineering", points: [{ x: d("2026-10-01"), y: 40 }, { x: d("2026-10-02"), y: 44 }, { x: d("2026-10-03"), y: 47 }] },
  ];

  it("prints its summary, draws one line per series and one focus stop per day", () => {
    const { container } = render(<LineChart title="Open roles" summary="142 open now, up 12 in 30 days" empty={empty} series={series} />);
    expect(screen.getByText("142 open now, up 12 in 30 days")).toBeInTheDocument();
    expect(container.querySelectorAll("[data-line]")).toHaveLength(2);
    expect(marks(container)).toHaveLength(3);
    // Two series: a legend names both (identity is never colour alone).
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getByText("All roles")).toBeInTheDocument();
    expect(within(legend).getByText("Engineering")).toBeInTheDocument();
  });

  it("names line ends directly only when every name fits its gutter; the legend always names them", () => {
    // 640px wide (the test width): short names fit the end gutter and sit at the line ends (apart, so they never collide).
    const apart = [{ ...series[0]!, points: series[0]!.points.map((p, i) => (i === 2 ? { ...p, y: 1100 } : p)) }, series[1]!];
    const short = render(<LineChart title="Stars" summary="s" empty={empty} series={apart} />);
    expect([...short.container.querySelectorAll("[data-end-label]")].map((t) => t.textContent)).toEqual(["All roles", "Engineering"]);
    short.unmount();
    // One name too long for the gutter: no end label at all (none clipped, none past the plot), the legend names every series.
    const long = [apart[0]!, { ...series[1]!, key: "nancy", label: "sonatype-nexus-community/nancy" }];
    const { container } = render(<LineChart title="Stars" summary="s" empty={empty} series={long} />);
    expect(container.querySelectorAll("[data-end-label]")).toHaveLength(0);
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["All roles", "sonatype-nexus-community/nancy"]);
  });

  it("shows every series' exact value for the focused day", () => {
    const { container } = render(<LineChart title="Open roles" summary="s" empty={empty} series={series} />);
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("2 Oct 2026");
    expect(tooltip()).toHaveTextContent("1,240");
    expect(tooltip()).toHaveTextContent("All roles");
    expect(tooltip()).toHaveTextContent("44");
    expect(tooltip()).toHaveTextContent("Engineering");
  });

  it("moves between days with the arrow keys, one tab stop for the whole chart", () => {
    const { container } = render(<LineChart title="Open roles" summary="s" empty={empty} series={series} />);
    const stops = marks(container);
    expect(stops.filter((m) => m.getAttribute("tabindex") === "0")).toHaveLength(1);
    focus(stops[0]!);
    fireEvent.keyDown(stops[0]!, { key: "ArrowRight" });
    expect(document.activeElement).toBe(stops[1]);
    expect(tooltip()).toHaveTextContent("1,240");
    fireEvent.keyDown(stops[1]!, { key: "End" });
    expect(document.activeElement).toBe(stops[2]);
  });

  it("colours series in the order given and folds more than four into Other", () => {
    const many = ["a", "b", "c", "d", "e", "f"].map((key, i) => ({
      key,
      label: key.toUpperCase(),
      points: [{ x: d("2026-10-01"), y: 10 - i }, { x: d("2026-10-02"), y: 12 - i }],
    }));
    const { container } = render(<LineChart title="By team" summary="s" empty={empty} series={many} />);
    const lines = [...container.querySelectorAll("[data-line]")];
    expect(lines).toHaveLength(5);
    expect(lines.map((l) => l.getAttribute("data-color"))).toEqual(["chart-1", "chart-2", "chart-3", "chart-4", "chart-5"]);
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("Other (2)")).toBeInTheDocument();
    focus(marks(container)[0]!);
    // Other sums the folded series: E 6 + F 5.
    expect(tooltip()).toHaveTextContent("11");
  });

  it("draws a caller-built Other as the kit's own: outside the named cap, dashed in the Other grey, its members named and in the table twin", () => {
    const days = ["2026-10-01", "2026-10-02", "2026-10-03"];
    const named = ["a", "b", "c", "d"].map((key, i) => ({ key, label: key.toUpperCase(), points: days.map((day) => ({ x: d(day), y: 10 - i })) }));
    const e = { key: "e", label: "E", points: days.map((day) => ({ x: d(day), y: 1 })) };
    const f = { key: "f", label: "F", points: days.slice(1).map((day) => ({ x: d(day), y: 2 })) };
    // The caller sums E and F from the first day both are measured, so the line never steps up on F's first day.
    const other = { key: "rest", label: "Other", points: days.slice(1).map((day) => ({ x: d(day), y: 3 })), members: [e, f] };
    const { container } = render(<LineChart title="By competitor" summary="s" empty={empty} series={[...named, other]} />);
    const lines = [...container.querySelectorAll("[data-line]")];
    expect(lines.map((l) => l.getAttribute("data-color"))).toEqual(["chart-1", "chart-2", "chart-3", "chart-4", "chart-5"]);
    const legend = within(screen.getByRole("list", { name: "Legend" }));
    expect(legend.getByText("Other (2)").closest("li")).toHaveAttribute("title", "E, F");
    // Its line is the caller's points: nothing on 1 Oct, 3 from 2 Oct.
    focus(marks(container)[0]!);
    expect(tooltip()).toHaveTextContent(/—\s*Other \(2\)/);
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(tooltip()).toHaveTextContent(/3\s*Other \(2\)/);
    // The table twin unfolds it: each member's exact values, as for the kit's own fold.
    expect(within(container).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Date", "A", "B", "C", "D", "E", "F"]);
  });

  it("never draws a fractional tick for whole counts", () => {
    const small = [{ key: "x", label: "Roles", points: [{ x: d("2026-10-01"), y: 0 }, { x: d("2026-10-02"), y: 1 }, { x: d("2026-10-03"), y: 2 }] }];
    const { container } = render(<LineChart title="Roles" summary="s" empty={empty} series={small} />);
    const ticks = [...container.querySelectorAll("[data-tick='y']")].map((t) => t.textContent);
    expect(ticks.length).toBeGreaterThan(1);
    expect(ticks.every((t) => /^\d+$/.test(t ?? ""))).toBe(true);
  });

  it("renders the empty state for no series or no points", () => {
    const { container, rerender } = render(<LineChart title="Open roles" summary="142 open" empty={empty} series={[]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    rerender(<LineChart title="Open roles" summary="142 open" empty={empty} series={[{ key: "a", label: "A", points: [] }]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(screen.queryByText("142 open")).toBeNull();
  });
});

describe("StackedArea", () => {
  const layers = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      key: `k${i}`,
      label: `Dept ${i + 1}`,
      points: [{ x: d("2026-10-01"), y: 20 - i }, { x: d("2026-10-02"), y: 22 - i }],
    }));

  it("stacks one layer per series with a focus stop per day, and totals the day in the tooltip", () => {
    const { container } = render(<StackedArea title="Open roles by department" summary="Engineering leads" empty={empty} series={layers(3)} />);
    expect(screen.getByText("Engineering leads")).toBeInTheDocument();
    expect(container.querySelectorAll("[data-layer]")).toHaveLength(3);
    expect(marks(container)).toHaveLength(2);
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("22");
    expect(tooltip()).toHaveTextContent("21");
    expect(tooltip()).toHaveTextContent("20");
    expect(tooltip()).toHaveTextContent("Total");
    expect(tooltip()).toHaveTextContent("63");
  });

  it("collapses more than five categories to the top four plus Other", () => {
    const { container } = render(<StackedArea title="By department" summary="s" empty={empty} series={layers(13)} />);
    const drawn = [...container.querySelectorAll("[data-layer]")];
    expect(drawn).toHaveLength(5);
    expect(drawn.at(-1)!.getAttribute("data-color")).toBe("chart-5");
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("Other (9)")).toBeInTheDocument();
  });

  it("renders the empty state for no data", () => {
    const { container } = render(<StackedArea title="By department" summary="s" empty={empty} series={[]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("BarList", () => {
  const departments = Array.from({ length: 13 }, (_, i) => ({ key: `d${i}`, label: `Department ${i + 1}`, value: 30 - i * 2 }));

  it("caps at N rows plus Other, which sums the rest", () => {
    render(<BarList title="Open roles by department" summary="Engineering leads" empty={empty} items={departments} max={8} />);
    expect(screen.getByText("Engineering leads")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(9);
    // Departments 9-13: 14 + 12 + 10 + 8 + 6 = 50.
    expect(rows.at(-1)).toHaveTextContent("Other (5)");
    expect(rows.at(-1)).toHaveTextContent("50");
    // Values are printed at the bar tip, exact.
    expect(rows[0]).toHaveTextContent("30");
  });

  it("lets a row that means something wear its meaning token, the rest the series slot", () => {
    const { container } = render(
      <BarList title="Stories by stance" summary="s" empty={empty} items={[{ key: "t", label: "Threat", value: 5, color: "threat" }, { key: "x", label: "Other story", value: 2 }]} />,
    );
    const fills = [...container.querySelectorAll<HTMLElement>("[data-bar-fill]")];
    expect(fills[0]!.className).toMatch(/(^|\s)bg-threat(\s|$)/);
    expect(fills[1]!.className).toMatch(/(^|\s)bg-chart-1(\s|$)/);
    // Its word still says it: the row's label and the mark's name.
    expect(screen.getByRole("img", { name: /^Threat: 5/ })).toBeInTheDocument();
  });

  it("prints a signed change with its direction as shape, not colour", () => {
    render(
      <BarList
        title="Tags"
        summary="s"
        empty={empty}
        deltaLabel="in 30 days"
        items={[{ key: "a", label: "Supply chain", value: 12, delta: 3 }, { key: "b", label: "SBOM", value: 5, delta: -2 }, { key: "c", label: "SAST", value: 4, delta: 0 }]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("+3");
    expect(rows[0]!.querySelector("[data-direction='up']")).not.toBeNull();
    expect(rows[1]).toHaveTextContent("−2");
    expect(rows[1]!.querySelector("[data-direction='down']")).not.toBeNull();
    expect(rows[2]!.querySelector("[data-direction]")).toBeNull();
  });

  it("shows the exact value and its share on focus", () => {
    const { container } = render(<BarList title="Departments" summary="s" empty={empty} items={departments.slice(0, 3)} />);
    focus(marks(container)[0]!);
    // 30 of 30 + 28 + 26 = 84 → 36%.
    expect(tooltip()).toHaveTextContent("30");
    expect(tooltip()).toHaveTextContent("36%");
  });

  it("renders the empty state for no items", () => {
    render(<BarList title="Departments" summary="s" empty={empty} items={[]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });
});

describe("StepLine", () => {
  const rounds = [
    { key: "seed", x: d("2021-03-01"), amount: 5_000_000, label: "Seed" },
    { key: "a", x: d("2022-06-15"), amount: null, label: "Series A" },
    { key: "b", x: d("2024-03-12"), amount: 45_000_000, label: "Series B" },
  ];

  it("draws the running total as one step line, a column per stated amount and a stop per step", () => {
    const { container } = render(<StepLine title="Funding" summary="$50M raised in 3 rounds" empty={empty} steps={rounds} valueFormat={usdM} />);
    expect(screen.getByText("$50M raised in 3 rounds")).toBeInTheDocument();
    expect(container.querySelectorAll("[data-line]")).toHaveLength(1);
    expect(container.querySelectorAll("[data-column]")).toHaveLength(2);
    expect(marks(container)).toHaveLength(3);
  });

  it("shows the step's amount and the running total, and says when an amount was not disclosed", () => {
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={rounds} valueFormat={usdM} />);
    focus(marks(container)[2]!);
    expect(tooltip()).toHaveTextContent("Series B");
    expect(tooltip()).toHaveTextContent("$45M");
    expect(tooltip()).toHaveTextContent("$50M");
    expect(tooltip()).toHaveTextContent("12 Mar 2024");
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("Amount not disclosed");
  });

  it("renders the empty state for no steps", () => {
    render(<StepLine title="Funding" summary="s" empty={empty} steps={[]} valueFormat={usdM} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });

  it("draws no money axis and no $0 when no step states an amount", () => {
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={[rounds[1]!]} valueFormat={usdM} />);
    expect(container.querySelectorAll("[data-tick='y']")).toHaveLength(0);
    expect(container.querySelectorAll("[data-line]")).toHaveLength(0);
    expect(container.querySelector("[data-plot]")).toHaveTextContent("Amounts not disclosed");
    expect(container.querySelector("[data-plot]")).not.toHaveTextContent("$");
    focus(marks(container)[0]!);
    expect(tooltip()).toHaveTextContent("Amount not disclosed");
    expect(tooltip()).not.toHaveTextContent("$");
  });

  it("reads a total after an undisclosed step as a lower bound: ≥ $X disclosed", () => {
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={rounds} valueFormat={usdM} />);
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("Disclosed total")).toBeInTheDocument();
    expect(container.querySelector("[data-end-label]")).toHaveTextContent("≥ $50M disclosed");
    focus(marks(container)[0]!);
    expect(tooltip()).toHaveTextContent("$5M");
    expect(tooltip()).not.toHaveTextContent("≥");
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("≥ $5M");
    focus(marks(container)[2]!);
    expect(tooltip()).toHaveTextContent("≥ $50M");
  });
});

describe("DotTimeline", () => {
  const points = [
    { key: "1", x: d("2025-01-10"), y: 900, detail: "LinkedIn About page" },
    { key: "2", x: d("2025-06-01"), y: 1100, detail: "TechCrunch" },
    { key: "3", x: d("2025-06-01"), y: 1250, detail: "Company blog", disputed: true },
    { key: "4", x: d("2026-02-01"), y: 1200, detail: "Press release" },
  ];
  const layoffs = [{ key: "l1", x: d("2025-09-01"), label: "Layoff", detail: "About 120 roles cut" }];

  it("draws a dot per reported figure and a marker per event, and never a line between them", () => {
    const { container } = render(<DotTimeline title="Headcount" summary="About 1,200 people" empty={empty} points={points} events={layoffs} />);
    expect(screen.getByText("About 1,200 people")).toBeInTheDocument();
    expect(marks(container, "point")).toHaveLength(4);
    expect(marks(container, "event")).toHaveLength(1);
    expect(container.querySelectorAll("svg path")).toHaveLength(0);
  });

  it("keeps two figures for the same month as two points, each with its source", () => {
    const { container } = render(<DotTimeline title="Headcount" summary="s" empty={empty} points={points} events={layoffs} />);
    const dots = marks(container, "point");
    focus(dots[1]!);
    expect(tooltip()).toHaveTextContent("1,100");
    expect(tooltip()).toHaveTextContent("TechCrunch");
    focus(dots[2]!);
    expect(tooltip()).toHaveTextContent("1,250");
    expect(tooltip()).toHaveTextContent("Sources disagree");
    focus(marks(container, "event")[0]!);
    expect(tooltip()).toHaveTextContent("About 120 roles cut");
  });

  it("renders the empty state for no figures", () => {
    render(<DotTimeline title="Headcount" summary="s" empty={empty} points={[]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });

  it("draws events alone on a bare time axis, with no value scale", () => {
    const { container } = render(<DotTimeline title="Headcount" summary="s" empty={empty} points={[]} events={layoffs} />);
    expect(marks(container, "event")).toHaveLength(1);
    expect(container.querySelectorAll("[data-tick='y']")).toHaveLength(0);
  });
});

describe("Swimlane", () => {
  const lanes = [
    { key: "Launches", label: "Launches" },
    { key: "Pricing", label: "Pricing" },
    { key: "Money", label: "Money" },
  ];
  const events = [
    { key: "12", lane: "Launches", x: d("2026-09-01"), size: 0.9, title: "Launches Acme Registry" },
    { key: "13", lane: "Launches", x: d("2026-09-20"), size: 0.4, title: "Adds Helm support" },
    { key: "14", lane: "Money", x: d("2026-10-02"), size: 0.7, title: "Raises $45M" },
    { key: "15", lane: "Pricing", x: d("2026-10-10"), size: 0.2, title: "New free tier" },
  ];

  it("sizes dots over the importances in view, from r 3 to 9, so size tells them apart", () => {
    const close = [
      { key: "a", lane: "Launches", x: d("2026-09-01"), size: 0.4, title: "Low" },
      { key: "b", lane: "Pricing", x: d("2026-09-10"), size: 0.65, title: "Mid" },
      { key: "c", lane: "Money", x: d("2026-09-20"), size: 0.9, title: "High" },
    ];
    const { container } = render(<Swimlane title="Momentum" summary="3 moves" empty={empty} lanes={lanes} events={close} />);
    const dots = [...container.querySelectorAll("[data-mark] circle")].filter((c) => !/fill-(transparent|none)/.test(c.getAttribute("class") ?? ""));
    const radii = dots.map((c) => Number(c.getAttribute("r")));
    expect(Math.min(...radii)).toBe(3);
    expect(Math.max(...radii)).toBe(9);
    // area grows with importance: the middle one sits at 3 + 6 × √½
    expect(radii.find((r) => r > 3 && r < 9)).toBeCloseTo(3 + 6 * Math.SQRT1_2, 5);
  });

  it("prints every lane and draws one linked dot per event", () => {
    const onNavigate = vi.fn();
    const { container } = render(
      <Swimlane title="Momentum" summary="4 moves in 6 weeks" empty={empty} lanes={lanes} events={events} href={(e) => `/signals/${e.key}`} onNavigate={onNavigate} />,
    );
    expect(screen.getByText("4 moves in 6 weeks")).toBeInTheDocument();
    for (const lane of lanes) expect(screen.getAllByText(lane.label).length).toBeGreaterThan(0);
    expect(marks(container)).toHaveLength(4);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(4);
    expect(links[0]).toHaveAttribute("href", "/signals/12");
    fireEvent.click(links[0]!);
    expect(onNavigate).toHaveBeenCalledWith("/signals/12");
  });

  it("names the event and its day on focus", () => {
    const { container } = render(<Swimlane title="Momentum" summary="s" empty={empty} lanes={lanes} events={events} />);
    focus(marks(container)[2]!);
    expect(tooltip()).toHaveTextContent("Raises $45M");
    expect(tooltip()).toHaveTextContent("2 Oct 2026");
    expect(tooltip()).toHaveTextContent("Money");
  });

  it("renders the empty state when no lane has an event", () => {
    render(<Swimlane title="Momentum" summary="s" empty={empty} lanes={lanes} events={[]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });

  it("draws only the events inside its window", () => {
    const { container, rerender } = render(<Swimlane title="Momentum" summary="s" empty={empty} lanes={lanes} events={events} domain={[d("2026-09-15"), d("2026-10-31")]} />);
    expect(marks(container)).toHaveLength(3);
    rerender(<Swimlane title="Momentum" summary="s" empty={empty} lanes={lanes} events={events} domain={[d("2026-01-01"), d("2026-06-30")]} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });
});

describe("BubbleScatter", () => {
  const points = [
    { key: "acme", label: "Acme", x: 0.8, y: 1.6, size: 140, group: "1" },
    { key: "globex", label: "Globex", x: 0.3, y: 0.9, size: 20, group: "2" },
    { key: "initech", label: "Initech", x: 0.55, y: 1.1, size: 60, group: "1" },
  ];
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  const ratio = (n: number) => `${n.toFixed(1)}×`;

  it("draws a labelled bubble per point and a legend for the groups", () => {
    const { container } = render(
      <BubbleScatter
        title="Landscape"
        summary="Acme overlaps most and is accelerating"
        empty={empty}
        points={points}
        x={{ label: "Product overlap", format: pct }}
        y={{ label: "Momentum", format: ratio }}
        size={{ label: "Open roles" }}
        groups={[{ key: "1", label: "Tier 1" }, { key: "2", label: "Tier 2" }]}
      />,
    );
    expect(screen.getByText("Acme overlaps most and is accelerating")).toBeInTheDocument();
    expect(marks(container)).toHaveLength(3);
    expect(screen.getAllByText("Acme").length).toBeGreaterThan(0);
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getByText("Tier 1")).toBeInTheDocument();
    expect(within(legend).getByText("Tier 2")).toBeInTheDocument();
  });

  it("gives the exact position and size on focus", () => {
    const { container } = render(
      <BubbleScatter title="Landscape" summary="s" empty={empty} points={points} x={{ label: "Product overlap", format: pct }} y={{ label: "Momentum", format: ratio }} size={{ label: "Open roles" }} />,
    );
    focus(marks(container)[0]!);
    expect(tooltip()).toHaveTextContent("Acme");
    expect(tooltip()).toHaveTextContent("80%");
    expect(tooltip()).toHaveTextContent("1.6×");
    expect(tooltip()).toHaveTextContent("140");
    expect(tooltip()).toHaveTextContent("Open roles");
  });

  it("renders the empty state for no points", () => {
    render(<BubbleScatter title="Landscape" summary="s" empty={empty} points={[]} x={{ label: "Overlap" }} y={{ label: "Momentum" }} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });
});

describe("Sparkline", () => {
  const weeks = Array.from({ length: 13 }, (_, i) => ({ x: d(`2026-07-${String(i + 1).padStart(2, "0")}`), y: i % 4 }));

  it("keeps the tapped week when focus arrives after a touch lifts (pointerdown, pointerleave, then focus)", () => {
    render(<Sparkline label="Signals per week" values={weeks} />);
    const img = screen.getByRole("img", { name: /Signals per week/ });
    vi.spyOn(img, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 88, height: 24, right: 88, bottom: 24, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(img, { clientX: 4, pointerType: "touch" }); // the first week
    fireEvent.pointerLeave(img, { pointerType: "touch" });
    focus(img);
    expect(tooltip()).toHaveTextContent("1 Jul 2026");
    // A keyboard focus later, with no pointer down, still reads the last week.
    act(() => img.blur());
    focus(img);
    expect(tooltip()).toHaveTextContent("13 Jul 2026");
  });

  it("is one image whose name says what it shows, with the week's value on focus", () => {
    const { container } = render(<Sparkline label="Signals per week, last 13 weeks" values={weeks} />);
    const img = screen.getByRole("img", { name: /Signals per week, last 13 weeks/ });
    expect(img).toBeInTheDocument();
    expect(container.querySelectorAll("[data-line]")).toHaveLength(1);
    focus(img);
    expect(tooltip()).toHaveTextContent("13 Jul 2026");
  });

  it("says so instead of drawing a flat line when there is no data", () => {
    const { container } = render(<Sparkline label="Signals per week" values={null} emptyLabel="Insufficient data" />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("DivergingBars", () => {
  const weeks = [
    { key: "w1", x: d("2026-09-07"), above: 2.5, below: 0 },
    { key: "w2", x: d("2026-09-14"), above: 0, below: 1.25 },
    { key: "w3", x: d("2026-09-21"), above: 1, below: 3 },
  ];
  const sides = { above: { label: "Opportunity", color: "opportunity" as const }, below: { label: "Threat", color: "threat" as const } };

  it("draws each week's two sides of the baseline with a stop per week", () => {
    const { container } = render(<DivergingBars title="Pressure" summary="Threat is rising" empty={empty} rows={weeks} sides={sides} />);
    expect(screen.getByText("Threat is rising")).toBeInTheDocument();
    expect(marks(container)).toHaveLength(3);
    expect(container.querySelectorAll("[data-bar='above']")).toHaveLength(2);
    expect(container.querySelectorAll("[data-bar='below']")).toHaveLength(2);
    // The sides are named in words, not colour alone.
    expect(screen.getAllByText("Opportunity").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Threat").length).toBeGreaterThan(0);
  });

  it("shows both exact values on focus", () => {
    const { container } = render(<DivergingBars title="Pressure" summary="s" empty={empty} rows={weeks} sides={sides} valueFormat={(n) => n.toFixed(2)} />);
    focus(marks(container)[2]!);
    expect(tooltip()).toHaveTextContent("1.00");
    expect(tooltip()).toHaveTextContent("3.00");
    expect(tooltip()).toHaveTextContent("Threat");
  });

  it("renders the empty state when every week is zero", () => {
    render(<DivergingBars title="Pressure" summary="s" empty={empty} rows={[{ key: "w", x: d("2026-09-07"), above: 0, below: 0 }]} sides={sides} />);
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
  });
});

/*
 * The four-colour cap, series told apart without colour at every width,
 * honest amounts and sizes, the sparkline's words, first paint, plot-level hit, Escape, the
 * flipped tooltip, disputed rounds and the clipped Other bar.
 */
describe("chart caps, amounts and hit targets", () => {
  const at = (width: number) =>
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: width, bottom: 240, width, height: 240, toJSON: () => ({}) } as DOMRect);
  const days = (n: number) => Array.from({ length: n }, (_, i) => d(`2026-09-${String(i + 1).padStart(2, "0")}`));
  const lineSeries = (n: number, length = 30) =>
    Array.from({ length: n }, (_, k) => ({ key: `s${k}`, label: `Series ${k + 1}`, points: days(length).map((x, i) => ({ x, y: 100 - k * 10 + (i % 3) })) }));
  const legendShapes = () =>
    within(screen.getByRole("list", { name: "Legend" }))
      .getAllByRole("listitem")
      .map((li) => li.querySelector("[data-shape]")?.getAttribute("data-shape"));

  it("caps categorical series at four colours plus a neutral Other, whatever maxSeries asks", () => {
    const { container } = render(<LineChart title="Share of voice" summary="s" empty={empty} series={lineSeries(5, 4)} maxSeries={9 as 4} />);
    const groups = [...container.querySelectorAll("[data-series]")];
    expect(groups.map((g) => g.getAttribute("data-color"))).toEqual(["chart-1", "chart-2", "chart-3", "chart-4", "chart-5"]);
    expect(groups.at(-1)).toHaveAttribute("data-series", "__other");
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("Other (1)")).toBeInTheDocument();
  });

  it("draws at its measured width on the first paint", () => {
    at(390);
    const { container } = render(<LineChart title="Roles" summary="s" empty={empty} series={lineSeries(1)} />);
    expect(container.querySelector("[data-plot] svg")).toHaveAttribute("width", "390");
  });

  it("tells line series apart without colour at 390: a marker shape per series, repeated in the legend", () => {
    at(390);
    const { container } = render(<LineChart title="Stars" summary="s" empty={empty} series={lineSeries(6)} />);
    const groups = [...container.querySelectorAll("[data-series]")];
    expect(groups.map((g) => g.getAttribute("data-shape"))).toEqual(["circle", "square", "triangle", "diamond", "none"]);
    for (const g of groups.slice(0, 4)) {
      const shapes = [...g.querySelectorAll("[data-marker]")].map((m) => m.getAttribute("data-shape"));
      // Along the line, not only at its end, so a crossing can be followed at phone width.
      expect(shapes.length).toBeGreaterThan(2);
      expect(new Set(shapes)).toEqual(new Set([g.getAttribute("data-shape")]));
    }
    // "Other" is the dashed line.
    expect(groups.at(-1)!.querySelector("[data-line]")).toHaveAttribute("stroke-dasharray");
    expect(legendShapes()).toEqual(["circle", "square", "triangle", "diamond", "none"]);
  });

  it("tells stacked layers apart without colour at 390: shaped markers on each edge, the legend and in-band names", () => {
    at(390);
    const series = lineSeries(4).map((s, k) => ({ ...s, points: s.points.map((p) => ({ ...p, y: 40 + k })) }));
    const { container } = render(<StackedArea title="By department" summary="s" empty={empty} series={series} />);
    const layers = [...container.querySelectorAll("[data-layer]")];
    expect(layers.map((l) => l.getAttribute("data-shape"))).toEqual(["circle", "square", "triangle", "diamond"]);
    for (const l of layers) expect(l.querySelectorAll(`[data-marker][data-shape="${l.getAttribute("data-shape")}"]`).length).toBeGreaterThan(2);
    expect(legendShapes()).toEqual(["circle", "square", "triangle", "diamond"]);
    expect([...container.querySelectorAll("[data-band-label]")].map((t) => t.textContent)).toEqual(["Series 1", "Series 2", "Series 3", "Series 4"]);
  });

  it("finds the nearest day from anywhere on the plot, and leaves vertical scrolling to touch", () => {
    at(390);
    const { container } = render(<LineChart title="Roles" summary="s" empty={empty} series={[{ key: "a", label: "A", points: days(3).map((x, i) => ({ x, y: i + 1 })) }]} />);
    const hit = container.querySelector("[data-hit]")!;
    // touch-action sits on the plot's wrapping element (iOS Safari ignores it on an SVG child).
    expect(container.querySelector("[data-plot]")).toHaveClass("touch-pan-y");
    fireEvent.pointerMove(hit, { clientX: 389 });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
    fireEvent.pointerMove(hit, { clientX: 0 });
    expect(tooltip()).toHaveTextContent("1 Sept 2026");
    fireEvent.pointerLeave(hit);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("hides the tooltip on Escape and keeps focus on the point", () => {
    const { container } = render(<LineChart title="Roles" summary="s" empty={empty} series={lineSeries(1, 3)} />);
    const stop = marks(container).at(-1)!;
    focus(stop);
    expect(tooltip()).toBeInTheDocument();
    fireEvent.keyDown(stop, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.activeElement).toBe(stop);
    fireEvent.keyDown(stop, { key: "ArrowLeft" });
    expect(tooltip()).toBeInTheDocument();
  });

  it("anchors a flipped tooltip at the mark's bottom edge, so it never covers the mark", () => {
    const { rerender } = render(<ChartTooltip x={200} y={20} bottom={44} containerWidth={400} heading="Acme" />);
    expect(tooltip().style.top).toBe("44px");
    expect(tooltip().style.transform).not.toContain("-100%");
    rerender(<ChartTooltip x={200} y={200} bottom={224} containerWidth={400} heading="Acme" />);
    expect(tooltip().style.top).toBe("200px");
    expect(tooltip().style.transform).toContain("-100%");    // A crosshair's tooltip sits beside the column of points, never on it.
    rerender(<ChartTooltip x={100} y={12} placement="side" containerWidth={400} heading="Week" />);
    expect(tooltip().style.top).toBe("12px");
    expect(tooltip().style.transform).toBe("translate(12px, 0px)");
    rerender(<ChartTooltip x={300} y={12} placement="side" containerWidth={400} heading="Week" />);
    expect(tooltip().style.transform).toBe("translate(calc(-100% - 12px), 0px)");
  });

  it("draws an unknown size as a hollow neutral marker, named in the tooltip and the legend", () => {
    const points = [
      { key: "acme", label: "Acme", x: 0.8, y: 1.6, size: 140 },
      { key: "oscorp", label: "Oscorp", x: 0.2, y: 0.5, size: null },
    ];
    const { container } = render(
      <BubbleScatter title="Landscape" summary="s" empty={empty} points={points} x={{ label: "Overlap" }} y={{ label: "Momentum" }} size={{ label: "Open roles", missingLabel: "No job board" }} />,
    );
    const unknown = container.querySelector("[data-bubble][data-unknown]")!;
    expect(unknown).not.toBeNull();
    expect(unknown.getAttribute("class")).toContain("stroke-chart-5");
    expect(unknown.getAttribute("class")).not.toMatch(/fill-chart/);
    expect(unknown).toHaveAttribute("stroke-dasharray");
    expect(container.querySelectorAll("[data-bubble]:not([data-unknown])")).toHaveLength(1);
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("No job board")).toBeInTheDocument();
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("No job board");
    expect(tooltip()).not.toHaveTextContent("—");
  });

  it("lists only the groups it draws in the legend, and each keeps its slot (a filter never repaints)", () => {
    const groups = [
      { key: "1", label: "Tier 1" },
      { key: "2", label: "Tier 2" },
      { key: "3", label: "Tier 3" },
    ];
    const only2 = [{ key: "globex", label: "Globex", x: 0.3, y: 0.9, size: 20, group: "2" }];
    const { container } = render(<BubbleScatter title="Landscape" summary="s" empty={empty} points={only2} x={{ label: "Overlap" }} y={{ label: "Momentum" }} groups={groups} />);
    const legend = within(screen.getByRole("list", { name: "Legend" }));
    expect(legend.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Tier 2"]);
    expect(container.querySelector("[data-bubble]")!.getAttribute("class")).toContain("stroke-chart-2");
  });

  it("keeps a reference line's label clear of the bubbles", () => {
    const points = [
      { key: "edge", label: "Edge", x: 10, y: 1, size: 100 },
      { key: "low", label: "Low", x: 0, y: 0.2, size: 10 },
    ];
    const { container } = render(
      <BubbleScatter title="Landscape" summary="s" empty={empty} points={points} x={{ label: "Overlap", domain: [0, 10] }} y={{ label: "Momentum", domain: [0, 2] }} size={{ label: "Open roles" }} yRef={{ value: 1, label: "Steady" }} />,
    );
    const label = [...container.querySelectorAll("text")].find((t) => t.textContent === "Steady")!;
    expect(label).toBeDefined();
    const x = Number(label.getAttribute("x"));
    const y = Number(label.getAttribute("y"));
    const w = textWidth("Steady");
    const box = label.getAttribute("text-anchor") === "end" ? [x - w, y - 8, x, y + 8] : [x, y - 8, x + w, y + 8];
    const hit = marks(container)[0]!.querySelector("circle")!;
    const [cx, cy, r] = ["cx", "cy", "r"].map((a) => Number(hit.getAttribute(a)));
    const rad = r! - 2;
    const clear = box[2]! <= cx! - rad || box[0]! >= cx! + rad || box[3]! <= cy! - rad || box[1]! >= cy! + rad;
    expect(clear).toBe(true);
  });

  it("leaves out a quadrant caption that has no place clear of the bubbles, and keeps the others", () => {
    const points = [
      { key: "corner", label: "Corner", x: 0, y: 0, size: 100 },
      { key: "top", label: "Top", x: 10, y: 1, size: 5 },
    ];
    const { container } = render(
      <BubbleScatter
        title="Landscape"
        summary="s"
        empty={empty}
        points={points}
        x={{ label: "Overlap", domain: [0, 10] }}
        y={{ label: "Momentum", domain: [0, 2] }}
        size={{ label: "Open roles" }}
        quadrants={{ topRight: "High and busy", bottomLeft: "Low and quiet" }}
      />,
    );
    const texts = [...container.querySelectorAll("text")].map((t) => t.textContent);
    expect(texts).not.toContain("Low and quiet");
    expect(texts).toContain("High and busy");
    expect(marks(container)).toHaveLength(2);
  });

  it("names each unknown size by its own reason when points differ (a board found but not fetched yet)", () => {
    const points = [
      { key: "acme", label: "Acme", x: 0.8, y: 1.6, size: 140 },
      { key: "oscorp", label: "Oscorp", x: 0.2, y: 0.5, size: null, missingLabel: "No job board" },
      { key: "globex", label: "Globex", x: 0.5, y: 1.1, size: null, missingLabel: "First snapshot pending" },
    ];
    const { container } = render(
      <BubbleScatter title="Landscape" summary="s" empty={empty} points={points} x={{ label: "Overlap" }} y={{ label: "Momentum" }} size={{ label: "Open roles", missingLabel: "No open-roles count yet" }} />,
    );
    // The legend names the shared mark; each tooltip, accessible name and table cell says its own reason.
    expect(within(screen.getByRole("list", { name: "Legend" })).getByText("No open-roles count yet")).toBeInTheDocument();
    expect(marks(container)[2]).toHaveAccessibleName(/Globex, .*Open roles first snapshot pending/);
    focus(marks(container)[1]!);
    expect(tooltip()).toHaveTextContent("No job board");
    focus(marks(container)[2]!);
    expect(tooltip()).toHaveTextContent("First snapshot pending");
    const rows = [...container.querySelectorAll("tbody tr")].map((r) => r.textContent);
    expect(rows).toContain("Oscorp0.20.5No job board");
    expect(rows).toContain("Globex0.51.1First snapshot pending");
  });

  it("marks a round whose sources disagree with the DotTimeline ring; an undisclosed one is dashed", () => {
    const steps = [
      { key: "seed", x: d("2021-03-01"), amount: 5_000_000, label: "Seed", disputed: true },
      { key: "a", x: d("2022-06-15"), amount: null, label: "Series A" },
    ];
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={steps} valueFormat={usdM} />);
    expect(marks(container).map((m) => m.querySelector("[data-state]")?.getAttribute("data-state"))).toEqual(["disputed", "undisclosed"]);
    const legend = within(screen.getByRole("list", { name: "Legend" }));
    expect(legend.getByText("Sources disagree")).toBeInTheDocument();
    expect(legend.getByText("Amount not disclosed")).toBeInTheDocument();
    focus(marks(container)[0]!);
    expect(tooltip()).toHaveTextContent("Sources disagree");
  });

  it("names its empty state by its label, and keeps null (insufficient) apart from []", () => {
    const { rerender } = render(<Sparkline label="Signals per week" values={null} />);
    expect(screen.getByRole("img", { name: "Signals per week: Insufficient data" })).toHaveTextContent("Insufficient data");
    rerender(<Sparkline label="Signals per week" values={[]} />);
    expect(screen.getByRole("img", { name: "Signals per week: No data yet" })).toHaveTextContent("No data yet");
  });

  it("scales bars to the largest named row and breaks an Other bar that outgrows it", () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ key: `t${i}`, label: `Tag ${i + 1}`, value: 10 - i }));
    const { container } = render(<BarList title="Tags" summary="s" empty={empty} items={items} max={3} />);
    const bars = [...container.querySelectorAll<HTMLElement>("[data-bar-fill]")];
    expect(bars.map((b) => b.getAttribute("data-share"))).toEqual(["1", "0.9", "0.8", "1"]);
    const rows = screen.getAllByRole("listitem");
    expect(rows.at(-1)).toHaveTextContent("Other (7)");
    expect(rows.at(-1)).toHaveTextContent("28");
    expect(rows.at(-1)!.querySelector("[data-break]")).not.toBeNull();
    expect(rows[0]!.querySelector("[data-break]")).toBeNull();
  });
});

/*
 * No "≥ $0" before the first disclosed amount, no re-render or
 * layout read per pointermove on the same day, and a side tooltip that fits its side.
 */
describe("chart hover and tooltip placement", () => {
  const rect = (width: number, onHit?: () => void) =>
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (this.hasAttribute("data-hit")) onHit?.();
      return { x: 0, y: 0, left: 0, top: 0, right: width, bottom: 240, width, height: 240, toJSON: () => ({}) } as DOMRect;
    });
  const opening = [
    { key: "seed", x: d("2021-03-01"), amount: null, label: "Seed" },
    { key: "a", x: d("2022-06-15"), amount: 20_000_000, label: "Series A" },
    { key: "b", x: d("2024-03-12"), amount: 30_000_000, label: "Series B" },
  ];
  const stateOf = (m: Element) => m.querySelector("[data-state]")!;
  const startOf = (container: HTMLElement) => /^M([\d.]+),([\d.]+)/.exec(container.querySelector("[data-line]")!.getAttribute("d")!)!;

  it("draws no total and no $0 before the first disclosed amount; the opening round sits on the date axis", () => {
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={opening} valueFormat={usdM} />);
    const [seed, a, b] = marks(container);
    // The seed names no money at all, in its accessible name, its tooltip and its table row.
    expect(seed!.getAttribute("aria-label")).not.toContain("$");
    focus(seed!);
    expect(tooltip()).toHaveTextContent("Amount not disclosed");
    expect(tooltip()).not.toHaveTextContent("$");
    const seedRow = [...container.querySelectorAll("tbody tr")][0]!;
    expect(seedRow).not.toHaveTextContent("$");
    expect(seedRow).toHaveTextContent("—");
    // Once Series A is disclosed, the total is a lower bound (the seed's amount is unknown).
    focus(a!);
    expect(tooltip()).toHaveTextContent("≥ $20M");
    focus(b!);
    expect(tooltip()).toHaveTextContent("≥ $50M");
    expect(container.querySelector("[data-end-label]")).toHaveTextContent("≥ $50M disclosed");
    expect(container.querySelector("[data-plot]")).not.toHaveTextContent("≥ $0");
    // The total line starts at Series A, on the baseline; the seed's ring sits on that baseline (the date axis).
    const [, x0, y0] = startOf(container);
    expect(Number(x0)).toBeCloseTo(Number(stateOf(a!).getAttribute("cx")), 3);
    expect(Number(stateOf(seed!).getAttribute("cy"))).toBeCloseTo(Number(y0), 3);
    expect(Number(stateOf(seed!).getAttribute("cx"))).toBeLessThan(Number(x0));
    expect(stateOf(seed!)).toHaveAttribute("data-state", "undisclosed");
  });

  it("keeps a middle undisclosed round on the line at the disclosed total so far", () => {
    const middle = [
      { key: "seed", x: d("2021-03-01"), amount: 5_000_000, label: "Seed" },
      { key: "a", x: d("2022-06-15"), amount: null, label: "Series A" },
      { key: "b", x: d("2024-03-12"), amount: 45_000_000, label: "Series B" },
    ];
    const { container } = render(<StepLine title="Funding" summary="s" empty={empty} steps={middle} valueFormat={usdM} />);
    const [seed, a] = marks(container);
    expect(stateOf(a!).getAttribute("cy")).toBe(stateOf(seed!).getAttribute("cy"));
    focus(a!);
    expect(tooltip()).toHaveTextContent("≥ $5M");
    expect([...container.querySelectorAll("tbody tr")][1]).toHaveTextContent("≥ $5M");
  });

  it("re-renders and reads the plot's box only when the nearest day changes", () => {
    let reads = 0;
    rect(390, () => reads++);
    let commits = 0;
    const days = [d("2026-09-01"), d("2026-09-02"), d("2026-09-03")];
    const { container } = render(
      <Profiler id="chart" onRender={() => commits++}>
        <LineChart title="Roles" summary="s" empty={empty} series={[{ key: "a", label: "A", points: days.map((x, i) => ({ x, y: i + 1 })) }]} />
      </Profiler>,
    );
    const hit = container.querySelector("[data-hit]")!;
    commits = 0;
    reads = 0;
    fireEvent.pointerEnter(hit, { clientX: 380 });
    fireEvent.pointerMove(hit, { clientX: 380 });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
    expect(commits).toBe(1);
    // Many more moves over the same day: React may check once more before it bails out eagerly,
    // but the commits never grow with the moves, and the box is never read again.
    for (let x = 381; x < 390; x++) fireEvent.pointerMove(hit, { clientX: x });
    const settled = commits;
    expect(settled).toBeLessThanOrEqual(2);
    for (let x = 381; x < 390; x++) fireEvent.pointerMove(hit, { clientX: x });
    expect(commits).toBe(settled);
    expect(reads).toBe(1);
    fireEvent.pointerMove(hit, { clientX: 0 });
    expect(tooltip()).toHaveTextContent("1 Sept 2026");
    expect(commits).toBe(settled + 1);
    expect(reads).toBe(1);
  });

  it("caps a side tooltip at the room on its side, choosing the roomier side", () => {
    const { rerender } = render(<ChartTooltip x={150} y={12} placement="side" containerWidth={358} heading="Week" />);
    // 208px to the right, 150px to the left: it goes right and fits there.
    expect(tooltip().style.transform).toBe("translate(12px, 0px)");
    expect(parseFloat(tooltip().style.maxWidth)).toBeLessThanOrEqual(358 - 150 - 12);
    rerender(<ChartTooltip x={230} y={12} placement="side" containerWidth={358} heading="Week" />);
    expect(tooltip().style.transform).toBe("translate(calc(-100% - 12px), 0px)");
    expect(parseFloat(tooltip().style.maxWidth)).toBeLessThanOrEqual(230 - 12);
  });
});

/*
 * Guards. Chart marks wear only the chart tokens: chart-1..5, the four meaning tokens and
 * the meter pair; chrome uses the neutral text and surface tokens. No colour literal and no font
 * size outside the four type tokens (the app-wide size guard lives in lib/utils.test.ts).
 */
const SOURCES = import.meta.glob<string>("/src/components/charts/*.tsx", { query: "?raw", import: "default", eager: true });
const theme = css.slice(css.indexOf("@theme inline {"));
const THEME_COLORS = new Set([...theme.matchAll(/^\s*--color-([a-z0-9-]+):/gm)].map((m) => m[1]!));
const MARKS = ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "threat", "opportunity", "neutral", "warning", "meter", "meter-track"];
const CHROME = ["foreground", "muted-foreground", "border", "input", "card", "background", "popover", "popover-foreground", "accent", "ring", "transparent", "current"];
const ALLOWED = new Set([...MARKS, ...CHROME]);

describe("token guard", () => {
  const files = Object.entries(SOURCES).filter(([path]) => !/\.test\.[^/]+$/.test(path));

  it("covers every chart file", () => {
    const names = files.map(([path]) => path.split("/").pop());
    for (const name of ["ChartFrame", "LineChart", "StackedArea", "BarList", "StepLine", "DotTimeline", "Swimlane", "BubbleScatter", "Sparkline", "DivergingBars", "scales", "tooltip"]) {
      expect(names).toContain(`${name}.tsx`);
    }
  });

  it("uses only the chart, meaning, meter and neutral chrome colour tokens", () => {
    const uses = files.flatMap(([path, source]) => [
      ...[...source.matchAll(/\b(?:fill|stroke|bg|text|border|outline|ring|decoration|shadow|accent|caret|from|via|to)-([a-z][a-z0-9-]*)/g)]
        .map((m) => m[1]!)
        .filter((token) => THEME_COLORS.has(token))
        .map((token) => `${path} ${token}`),
      ...[...source.matchAll(/var\(--([a-z0-9-]+)\)/g)].map((m) => `${path} ${m[1]}`),
    ]);
    expect(uses.length).toBeGreaterThan(20);
    expect(uses.filter((use) => !ALLOWED.has(use.split(" ")[1]!))).toEqual([]);
  });

  it("has no colour literals and no font size outside the four type tokens", () => {
    for (const [path, source] of files) {
      expect(source, path).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/);
      expect(source, path).not.toMatch(/font-?size/i);
      const sizes = [...source.matchAll(/\btext-(meta|body|title|display|xs|sm|base|lg|[2-9]?xl|\[[^\]]+\])\b/g)].map((m) => m[1]);
      expect(sizes.filter((s) => !["meta", "body", "title", "display"].includes(s!)), path).toEqual([]);
    }
  });
});

/* Every chart colour is a mark (>= 3:1 on card and page, both themes) and no series hue impersonates a meaning token. */
function tokens(block: string) {
  return Object.fromEntries([...block.matchAll(/--([a-z0-9-]+):\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)/g)].map((m) => [m[1]!, [Number(m[2]), Number(m[3]), Number(m[4])] as const]));
}
function luminance([L, C, h]: readonly [number, number, number]) {
  const a = C * Math.cos((h * Math.PI) / 180);
  const b = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  const [r, g, bl] = rgb.map((c) => Math.min(1, Math.max(0, c)));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * bl!;
}
const ratio = (x: readonly [number, number, number], y: readonly [number, number, number]) => {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi! + 0.05) / (lo! + 0.05);
};
const oklabDistance = ([L1, C1, h1]: readonly [number, number, number], [L2, C2, h2]: readonly [number, number, number]) => {
  const r = Math.PI / 180;
  return 100 * Math.hypot(L1 - L2, C1 * Math.cos(h1 * r) - C2 * Math.cos(h2 * r), C1 * Math.sin(h1 * r) - C2 * Math.sin(h2 * r));
};

describe("chart palette", () => {
  const light = tokens(css.slice(css.indexOf(":root {"), css.indexOf(".dark {")));
  const dark = tokens(css.slice(css.indexOf(".dark {"), css.indexOf("@theme inline {")));

  it.each([
    ["light", light],
    ["dark", dark],
  ])("every chart colour clears 3:1 on the card and the page (%s)", (_, t) => {
    for (const name of ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "threat", "opportunity", "meter"]) {
      expect(ratio(t[name]!, t.card!), `${name} on card`).toBeGreaterThanOrEqual(3);
      expect(ratio(t[name]!, t.background!), `${name} on background`).toBeGreaterThanOrEqual(3);
    }
  });

  it.each([
    ["light", light],
    ["dark", dark],
  ])("no categorical slot reads as threat, opportunity or warning (%s)", (_, t) => {
    for (const slot of ["chart-1", "chart-2", "chart-3", "chart-4"]) {
      for (const meaning of ["threat", "opportunity", "warning"]) {
        expect(oklabDistance(t[slot]!, t[meaning]!), `${slot} vs ${meaning}`).toBeGreaterThanOrEqual(15);
      }
    }
  });
});

it("pins the d3 modules to exact versions", () => {
  const { dependencies, devDependencies } = JSON.parse(pkg) as { dependencies: Record<string, string>; devDependencies: Record<string, string> };
  expect(dependencies).toMatchObject({ "d3-scale": "4.0.2", "d3-shape": "3.2.0", "d3-array": "3.2.4" });
  expect(devDependencies).toMatchObject({ "@types/d3-scale": "4.0.8", "@types/d3-shape": "3.1.6", "@types/d3-array": "3.2.1" });
});

/*
 * touch and hover. A lifted finger leaves the readout up until the
 * next tap elsewhere; touch-action sits on the plot's wrapping element (iOS Safari ignores it on
 * SVG children); a break mark is a real gap, so a hovered row's wash shows through it; the plot's
 * left edge is read again after a scroll or a resize.
 */
describe("touch and hover", () => {
  const box = (left: number, width = 390) =>
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({ x: left, y: 0, left, top: 0, right: left + width, bottom: 240, width, height: 240, toJSON: () => ({}) } as DOMRect);
  const week = [d("2026-09-01"), d("2026-09-02"), d("2026-09-03")];
  const line = () => <LineChart title="Roles" summary="s" empty={empty} series={[{ key: "a", label: "A", points: week.map((x, i) => ({ x, y: i + 1 })) }]} />;

  it("keeps a line chart's readout after a finger lifts, until the next tap elsewhere", () => {
    box(0);
    const { container } = render(line());
    const hit = container.querySelector("[data-hit]")!;
    fireEvent.pointerDown(hit, { clientX: 389, pointerType: "touch" });
    fireEvent.pointerUp(hit, { clientX: 389, pointerType: "touch" });
    fireEvent.pointerLeave(hit, { pointerType: "touch" });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
    // A tap on the plot moves it; a tap anywhere else puts it away.
    fireEvent.pointerDown(hit, { clientX: 0, pointerType: "touch" });
    fireEvent.pointerLeave(hit, { pointerType: "touch" });
    expect(tooltip()).toHaveTextContent("1 Sept 2026");
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("still hides the readout when a mouse leaves", () => {
    box(0);
    const { container } = render(line());
    const hit = container.querySelector("[data-hit]")!;
    fireEvent.pointerMove(hit, { clientX: 389, pointerType: "mouse" });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
    fireEvent.pointerLeave(hit, { pointerType: "mouse" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("keeps a tapped dot's and a tapped bar's readout too", () => {
    const { container, unmount } = render(<DotTimeline title="Headcount" summary="s" empty={empty} points={[{ key: "a", x: d("2026-03-01"), y: 900 }, { key: "b", x: d("2026-06-01"), y: 1250 }]} />);
    const dot = marks(container)[1]!;
    fireEvent.pointerEnter(dot, { pointerType: "touch" });
    fireEvent.pointerLeave(dot, { pointerType: "touch" });
    expect(tooltip()).toHaveTextContent("1,250");
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    unmount();

    render(<BarList title="Tags" summary="s" empty={empty} items={[{ key: "a", label: "Alpha", value: 4 }, { key: "b", label: "Beta", value: 2 }]} />);
    const row = screen.getAllByRole("listitem")[1]!;
    fireEvent.pointerEnter(row, { pointerType: "touch" });
    fireEvent.pointerLeave(row, { pointerType: "touch" });
    expect(tooltip()).toHaveTextContent("Beta");
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("sets touch-action on the plot's wrapping element, not on an SVG child", () => {
    const { container } = render(line());
    expect(container.querySelector("[data-plot]")).toHaveClass("touch-pan-y");
    expect(container.querySelector("[data-hit]")).not.toHaveClass("touch-pan-y");
    const { container: bars } = render(
      <DivergingBars title="Pressure" summary="s" empty={empty} rows={[{ key: "w", x: d("2026-10-01"), above: 1, below: 2 }]} sides={{ above: { label: "Opportunity", color: "opportunity" }, below: { label: "Threat", color: "threat" } }} />,
    );
    expect(bars.querySelector("[data-plot]")).toHaveClass("touch-pan-y");
  });

  it("re-reads the plot's left edge after a scroll or a resize, so the nearest day stays right", () => {
    const rect = box(0);
    const { container } = render(line());
    const hit = container.querySelector("[data-hit]")!;
    fireEvent.pointerEnter(hit, { clientX: 389 });
    fireEvent.pointerMove(hit, { clientX: 389 });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
    // The page scrolls sideways under a still pointer: the plot now starts 300px further right.
    rect.mockReturnValue({ x: 300, y: 0, left: 300, top: 0, right: 690, bottom: 240, width: 390, height: 240, toJSON: () => ({}) } as DOMRect);
    fireEvent.scroll(window);
    fireEvent.pointerMove(hit, { clientX: 300 });
    expect(tooltip()).toHaveTextContent("1 Sept 2026");
    rect.mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 390, bottom: 240, width: 390, height: 240, toJSON: () => ({}) } as DOMRect);
    fireEvent(window, new Event("resize"));
    fireEvent.pointerMove(hit, { clientX: 389 });
    expect(tooltip()).toHaveTextContent("3 Sept 2026");
  });

  it("cuts a broken Other bar with a real gap (a mask), never strokes painted in the surface colour", () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ key: `t${i}`, label: `Tag ${i + 1}`, value: 10 - i }));
    render(<BarList title="Tags" summary="s" empty={empty} items={items} max={3} surface="card" />);
    const other = screen.getAllByRole("listitem").at(-1)!;
    const cut = other.querySelector<HTMLElement>("[data-break]")!;
    expect(cut).toHaveAttribute("data-bar-fill");
    expect(cut.className).toMatch(/mask-image:linear-gradient/);
    // Nothing inside the bar wears a surface colour: on a hovered row's wash that read as pale slashes.
    expect(other.querySelector("[data-bar-fill] .bg-card, [data-bar-fill] .bg-background")).toBeNull();
  });
});

/*
 * a board first listed today is a series of one day. The scale pads a
 * day on each side for room, but no tick may name a day nothing was measured on, tomorrow least.
 */
describe("one-day series", () => {
  const ticks = (container: HTMLElement) => [...container.querySelectorAll('[data-tick="x"]')].map((t) => t.textContent);
  it("labels only the day measured, in a line chart and a stacked area", () => {
    const one = [{ key: "a", label: "A", points: [{ x: d("2026-10-20"), y: 142 }] }];
    const { container, unmount } = render(<LineChart title="Open roles" summary="s" empty={empty} series={one} zero={false} />);
    expect(ticks(container)).toEqual(["20 Oct"]);
    unmount();
    const { container: stacked } = render(<StackedArea title="Roles" summary="s" empty={empty} series={[one[0]!, { key: "b", label: "B", points: [{ x: d("2026-10-20"), y: 4 }] }]} />);
    expect(ticks(stacked)).toEqual(["20 Oct"]);
  });

  it("labels a short series at its own measured days, never between them (3 collected weeks)", () => {
    const weeks = [d("2026-09-30"), d("2026-10-07"), d("2026-10-14")];
    const { container } = render(<LineChart title="Weekly momentum" summary="s" empty={empty} series={[{ key: "m", label: "Momentum", points: weeks.map((x, i) => ({ x, y: 100 + i })) }]} />);
    expect(ticks(container)).toEqual(["30 Sept", "7 Oct", "14 Oct"]);
  });

  it("labels only the day of a single reported figure", () => {
    const { container } = render(<DotTimeline title="Headcount" summary="s" empty={empty} points={[{ key: "a", x: d("2026-10-20"), y: 1250 }]} />);
    expect(ticks(container)).toEqual(["20 Oct"]);
  });
});
