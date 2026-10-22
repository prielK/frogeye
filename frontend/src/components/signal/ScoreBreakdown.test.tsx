import { render, screen } from "@testing-library/react";
import { scoreBreakdown } from "@/test/fixtures";
import { ScoreBreakdown } from "./ScoreBreakdown";

it("shows the thin-evidence multiplier so the arithmetic adds up", () => {
  render(
    <ScoreBreakdown breakdown={{ ...scoreBreakdown(), importance: 37, evidence_depth: { thin: true, multiplier: 0.6 } }} />,
  );
  const row = screen.getByText(/thin evidence/i).closest("p");
  expect(row).toHaveTextContent(/headlines only/i);
  expect(row).toHaveTextContent("× 0.60");
  expect(screen.getByText(/importance 37/i)).toBeInTheDocument();
});

it("adds no evidence row when the evidence is not thin", () => {
  render(<ScoreBreakdown breakdown={{ ...scoreBreakdown(), evidence_depth: { thin: false, multiplier: 1 } }} />);
  expect(screen.queryByText(/thin evidence/i)).not.toBeInTheDocument();
  expect(screen.getByText(/importance 62/i)).toBeInTheDocument();
});

it("falls back to factors × freshness × evidence when the stored importance is missing", () => {
  const breakdown: Record<string, unknown> = { ...scoreBreakdown(), evidence_depth: { thin: true, multiplier: 0.6 } };
  delete breakdown.importance;
  render(<ScoreBreakdown breakdown={breakdown} />);
  const total = 0.8 * 0.3 + 0.6 * 0.15 + 0.15 + 0.9 * 0.15 + 0.07 + 0.08 + 0.33 * 0.05;
  expect(screen.getByText(new RegExp(`importance ${Math.round(100 * total * 0.79 * 0.6)}\\b`, "i"))).toBeInTheDocument();
});

it("shows the no-JFrog-product multiplier, so the factors still add up to the importance", () => {
  render(
    <ScoreBreakdown
      breakdown={{ ...scoreBreakdown(), importance: 43, product_impact: { none: true, multiplier: 0.7 } }}
    />,
  );
  const row = screen.getByText(/no jfrog product affected/i).closest("p");
  expect(row).toHaveTextContent("× 0.70");
  expect(screen.getByText(/importance 43/i)).toBeInTheDocument();
});

it("adds no product row when the analysis rates a product, and multiplies it into a missing importance", () => {
  render(<ScoreBreakdown breakdown={{ ...scoreBreakdown(), product_impact: { none: false, multiplier: 1 } }} />);
  expect(screen.queryByText(/no jfrog product affected/i)).not.toBeInTheDocument();
});

it("falls back to factors × freshness × relevance when the stored importance is missing", () => {
  const breakdown: Record<string, unknown> = { ...scoreBreakdown(), product_impact: { none: true, multiplier: 0.7 } };
  delete breakdown.importance;
  render(<ScoreBreakdown breakdown={breakdown} />);
  const total = 0.8 * 0.3 + 0.6 * 0.15 + 0.15 + 0.9 * 0.15 + 0.07 + 0.08 + 0.33 * 0.05;
  expect(screen.getByText(new RegExp(`importance ${Math.round(100 * total * 0.79 * 0.7)}\\b`, "i"))).toBeInTheDocument();
});

it("says the freshness age is the one the score used, and what it counts from", () => {
  const { rerender } = render(<ScoreBreakdown breakdown={{ ...scoreBreakdown(), age_from: "event_date" }} />);
  expect(screen.getByText(/freshness/i)).toHaveTextContent("Freshness when scored: 1 day old (from its event date)");
  rerender(<ScoreBreakdown breakdown={{ ...scoreBreakdown(), age_days: 0.4, age_from: "first_seen" }} />);
  expect(screen.getByText(/freshness/i)).toHaveTextContent("Freshness when scored: under a day old (from first seen)");
  rerender(<ScoreBreakdown breakdown={scoreBreakdown()} />); // scored before the basis was stored
  expect(screen.getByText(/freshness/i)).toHaveTextContent("Freshness when scored: 1 day old");
  expect(screen.getByText(/freshness/i)).not.toHaveTextContent("(from");
});
